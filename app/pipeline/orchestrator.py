"""
Оркестратор пайплайна: координирует все шаги обработки одной задачи.
Поддерживает resume с последнего успешного шага.
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, Callable

from telethon import TelegramClient

from app.config import Config
from app.db.database import Database
from app.pipeline.downloader import TelegramDownloader
from app.pipeline.transcriber import Transcriber
from app.pipeline.pdf_exporter import PDFExporter
from app.utils.url_parser import TelegramLink
from app.utils.cleanup import cleanup_after_success, cleanup_wav
from app.utils.async_utils import run_sync
from app.pipeline.downloader import (
    AccessDeniedError, MediaNotFoundError,
    UnsupportedMediaError, MediaLimitExceededError,
)

logger = logging.getLogger("tgassistant.orchestrator")


class PipelineError(Exception):
    """Ошибка пайплайна с информацией о шаге."""
    def __init__(self, message: str, step: str):
        super().__init__(message)
        self.step = step


class Orchestrator:
    def __init__(self, cfg: Config, db: Database, progress_cb: Optional[Callable] = None):
        self.cfg = cfg
        self.db = db
        self.downloader = TelegramDownloader(cfg)
        self.transcriber = Transcriber(cfg)
        self.exporter = PDFExporter(cfg)
        self._progress_cb = progress_cb

    def run(
        self,
        job_id: str,
        link: TelegramLink,
        client: TelegramClient,
        from_start: bool = False,
    ) -> dict:
        """
        Выполняет все шаги пайплайна с поддержкой resume.

        Args:
            job_id:     ID задачи в БД
            link:       распарсенная ссылка
            client:     авторизованный Telethon клиент
            from_start: если True — игнорировать кэш и запустить с нуля

        Returns:
            dict с путями к PDF
        """
        def _notify(status: str, **extra):
            if self._progress_cb:
                try:
                    self._progress_cb(job_id, status, **extra)
                except Exception:
                    pass  # SSE notification is best-effort

        if from_start:
            logger.info("Запуск с нуля (--from-start).")

        media_path: Optional[str] = None
        job = self.db.get_job_by_id(job_id)
        asset = self.db.get_asset(job_id)

        # ── Шаг A: DOWNLOAD ──────────────────────────────────
        if from_start or not asset or not asset.get("temp_path") or \
                not Path(asset.get("temp_path", "")).exists():

            self.db.update_job_status(job_id, "downloading")
            _notify("downloading")
            logger.info("Шаг 1/3: Скачиваю медиафайл...")

            try:
                result = run_sync(self.downloader.download(client, link))
                media_path, asset_type, mime_type, duration_sec, file_size = result
            except (AccessDeniedError, MediaNotFoundError,
                    UnsupportedMediaError, MediaLimitExceededError):
                raise  # пропускаем к worker.py как NON_RETRYABLE
            except Exception as e:
                raise PipelineError(str(e), step="download")

            self.db.save_asset(
                job_id=job_id,
                asset_type=asset_type,
                temp_path=media_path,
                mime_type=mime_type,
                file_size_bytes=file_size,
                duration_sec=duration_sec,
            )
            logger.info("✓ Шаг 1/3: Скачано → %s", media_path)
        else:
            media_path = asset["temp_path"]
            logger.info("Шаг 1/3: используем кэшированный файл → %s", media_path)

        # ── Шаг B: TRANSCRIBE ────────────────────────────────
        transcript_record = None if from_start else self.db.get_transcript(job_id)

        if not transcript_record:
            self.db.update_job_status(job_id, "transcribing")
            _notify("transcribing")
            logger.info("Шаг 2/3: Транскрибирую...")

            try:
                transcript = self.transcriber.transcribe(media_path, job_id)
            except Exception as e:
                raise PipelineError(str(e), step="transcribe")

            self.db.save_transcript(
                job_id=job_id,
                full_text=transcript.full_text,
                segments=transcript.to_segments_json(),
                language=transcript.language,
                model_used=transcript.model_used,
                duration_sec=transcript.duration_sec,
                word_count=transcript.word_count,
                unrecognized_count=transcript.unrecognized_count,
            )
            # После успешной транскрипции медиа-файл больше не нужен
            cleanup_after_success(media_path)
            self.db.mark_asset_deleted(job_id)
            logger.info(
                "✓ Шаг 2/3: Транскрибировано (%d слов, язык: %s)",
                transcript.word_count, transcript.language,
            )
        else:
            # Восстанавливаем из базы
            from app.pipeline.transcriber import TranscriptResult, Segment
            import json
            segs_raw = transcript_record.get("segments") or []
            segments = [
                Segment(
                    start=s["start"],
                    end=s["end"],
                    text=s["text"],
                    avg_logprob=s.get("avg_logprob", 0.0),
                )
                for s in segs_raw
            ]
            transcript = TranscriptResult(
                segments=segments,
                language=transcript_record["language"] or "ru",
                model_used=transcript_record["model_used"] or "",
                duration_sec=transcript_record.get("duration_sec"),
                full_text=transcript_record["full_text"],
                word_count=transcript_record.get("word_count", 0),
                unrecognized_count=transcript_record.get("unrecognized_count", 0),
            )
            logger.info("Шаг 2/3: используем кэшированный транскрипт из БД.")

        # ── Шаг C: EXPORT PDF ────────────────────────────────
        exports = [] if from_start else self.db.get_exports(job_id)
        existing_types = {e["export_type"] for e in exports}

        self.db.update_job_status(job_id, "exporting")
        _notify("exporting")
        logger.info("Шаг 3/3: Создаю PDF...")

        pdf_paths = {}

        if "transcript" not in existing_types:
            try:
                t_path = self.exporter.export_transcript(transcript, link)
            except Exception as e:
                raise PipelineError(str(e), step="export_transcript")
            t_size = Path(t_path).stat().st_size
            self.db.save_export(job_id, "transcript", t_path, t_size)
            pdf_paths["transcript"] = t_path
        else:
            existing = next(e for e in exports if e["export_type"] == "transcript")
            pdf_paths["transcript"] = existing["file_path"]

        logger.info("✓ Шаг 3/3: PDF создан.")

        # ── DONE ─────────────────────────────────────────────
        self.db.update_job_status(job_id, "done")

        return pdf_paths
