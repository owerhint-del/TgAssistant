"""
External Collector: пайплайн сбора данных с YouTube, X, VK, Rutube и других платформ через yt-dlp.

Собирает видео в папку:
  - meta.json           — метаданные видео (title, uploader, duration и т.д.)
  - description.txt     — описание видео verbatim (если есть)
  - transcript.txt      — транскрипция Whisper с таймкодами
  - attachments/        — видеофайл + thumbnail
  - manifest.json       — маркер завершения (пишется ПОСЛЕДНИМ)

Структура вывода:
  <output_dir>/collected/external/<source>/<video_id>/
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

from app.config import Config
from app.db.database import Database
from app.utils.url_parser import ExternalLink
from app.pipeline.downloader import MediaLimitExceededError

logger = logging.getLogger("tgassistant.external_collector")


class ExternalCollectorError(Exception):
    """Ошибка external collector пайплайна (retryable)."""
    def __init__(self, message: str, step: str = "external"):
        super().__init__(message)
        self.step = step


def _check_ytdlp():
    """Проверяет наличие yt-dlp. Выбрасывает ImportError с понятным сообщением."""
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        raise ImportError(
            "yt-dlp не установлен. Установи: pip install yt-dlp"
        )


class ExternalCollectorOrchestrator:
    def __init__(self, cfg: Config, db: Database, progress_cb: Optional[Callable] = None):
        self.cfg = cfg
        self.db = db
        self._progress_cb = progress_cb

    def _collected_dir(self, link: ExternalLink) -> Path:
        """Путь к папке collected для данного видео."""
        return Path(self.cfg.output_dir) / "collected" / "external" / link.source / link.video_id

    def run(
        self,
        job_id: str,
        link: ExternalLink,
        from_start: bool = False,
    ) -> dict:
        """
        Скачивает видео через yt-dlp, транскрибирует через Whisper.

        Returns:
            dict с ключом 'collected_dir' — путь к папке
        """
        _check_ytdlp()
        import yt_dlp

        def _notify(status: str, **extra):
            if self._progress_cb:
                try:
                    self._progress_cb(job_id, status, **extra)
                except Exception:
                    pass

        collected_dir = self._collected_dir(link)

        # ── 1. IDEMPOTENCY CHECK ──────────────────────────────
        if not from_start and (collected_dir / "manifest.json").exists():
            logger.info("External collecting уже выполнен, кэш: %s", collected_dir)
            self.db.update_job_status(job_id, "done")
            exports = self.db.get_exports(job_id)
            if not any(e["export_type"] == "collected" for e in exports):
                self.db.save_export(job_id, "collected", str(collected_dir))
            return {"collected_dir": str(collected_dir)}

        # ── 2. ANALYZING ──────────────────────────────────────
        self.db.update_job_status(job_id, "analyzing")
        _notify("analyzing")
        logger.info("External collector: анализирую %s (%s)", link.source, link.raw_url)

        try:
            info = self._get_info(link.raw_url)
        except yt_dlp.utils.DownloadError as e:
            err_msg = str(e)
            if "Sign in" in err_msg or "age" in err_msg.lower():
                raise ExternalCollectorError(
                    "Видео с ограничением по возрасту. Укажи YTDLP_COOKIES_FILE в конфиге.",
                    step="fetch_info",
                )
            raise ExternalCollectorError(
                f"Видео не найдено или недоступно: {err_msg}",
                step="fetch_info",
            )
        except Exception as e:
            raise ExternalCollectorError(
                f"Ошибка получения информации о видео: {e}",
                step="fetch_info",
            )

        if not info:
            raise ExternalCollectorError(
                "Видео не найдено или недоступно.",
                step="fetch_info",
            )

        # Проверяем лимиты
        duration = info.get("duration") or 0
        if duration > self.cfg.max_duration_sec:
            dur_min = int(duration / 60)
            max_min = int(self.cfg.max_duration_sec / 60)
            raise MediaLimitExceededError(
                f"Видео слишком длинное: {dur_min} мин (максимум {max_min} мин)."
            )

        filesize = info.get("filesize") or info.get("filesize_approx") or 0
        max_bytes = self.cfg.max_file_mb * 1024 * 1024
        if filesize and filesize > max_bytes:
            size_mb = int(filesize / 1024 / 1024)
            raise MediaLimitExceededError(
                f"Файл слишком большой: {size_mb} МБ (максимум {self.cfg.max_file_mb} МБ)."
            )

        # Создаём структуру
        collected_dir.mkdir(parents=True, exist_ok=True)
        attachments_dir = collected_dir / "attachments"
        attachments_dir.mkdir(exist_ok=True)

        # Сохраняем описание
        description = info.get("description") or ""
        if description:
            (collected_dir / "description.txt").write_text(description, encoding="utf-8")
            logger.info("  Описание сохранено: %d символов", len(description))

        # ── 3. DOWNLOADING ────────────────────────────────────
        self.db.update_job_status(job_id, "downloading")
        _notify("downloading")
        logger.info("External collector: скачиваю видео...")

        try:
            video_path = self._download_video(link.raw_url, collected_dir)
        except yt_dlp.utils.DownloadError as e:
            raise ExternalCollectorError(
                f"Ошибка скачивания видео: {e}",
                step="download",
            )
        except Exception as e:
            raise ExternalCollectorError(
                f"Ошибка скачивания видео: {e}",
                step="download",
            )

        # ── 4. TRANSCRIBING ───────────────────────────────────
        self.db.update_job_status(job_id, "transcribing")
        _notify("transcribing")
        logger.info("External collector: транскрибирую видео...")

        transcript_language = None
        transcript_word_count = 0
        has_transcript = False

        try:
            transcript_language, transcript_word_count = self._transcribe_video(
                video_path, collected_dir, job_id
            )
            has_transcript = True
        except Exception as e:
            raise ExternalCollectorError(
                f"Ошибка транскрипции: {e}",
                step="transcribe",
            )

        # ── 5. SAVING ────────────────────────────────────────
        self.db.update_job_status(job_id, "saving")
        _notify("saving")
        logger.info("External collector: записываю метаданные...")

        # Собираем информацию о файлах в attachments/
        downloaded_files = []
        for f in attachments_dir.iterdir():
            if f.is_file():
                file_type = "video"
                mime_type = "video/mp4"
                if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                    file_type = "thumbnail"
                    mime_type = f"image/{f.suffix.lstrip('.').lower()}"
                    if mime_type == "image/jpg":
                        mime_type = "image/jpeg"
                downloaded_files.append({
                    "type": file_type,
                    "filename": f.name,
                    "mime_type": mime_type,
                    "file_size_bytes": f.stat().st_size,
                    "path": f"attachments/{f.name}",
                })

        # meta.json
        meta = self._build_meta(
            info=info,
            link=link,
            downloaded_files=downloaded_files,
            has_transcript=has_transcript,
            transcript_language=transcript_language,
            transcript_word_count=transcript_word_count,
        )
        (collected_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # manifest.json (ПОСЛЕДНИМ)
        manifest = self._build_manifest(
            collected_dir=collected_dir,
            downloaded_files=downloaded_files,
            has_transcript=has_transcript,
            has_description=bool(description),
        )
        (collected_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # DB export record
        self.db.save_export(job_id, "collected", str(collected_dir))

        # Done
        self.db.update_job_status(job_id, "done")
        _notify("done")
        logger.info("External collector завершён: %s", collected_dir)

        return {"collected_dir": str(collected_dir)}

    # ── yt-dlp helpers ─────────────────────────────────────────

    def _get_info(self, url: str) -> dict:
        """Получить метаданные видео без скачивания."""
        import yt_dlp

        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "no_playlist": True,
        }
        if self.cfg.ytdlp_cookies_file:
            opts["cookiefile"] = self.cfg.ytdlp_cookies_file

        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    def _download_video(self, url: str, output_dir: Path) -> Path:
        """Скачать видео в attachments/. Возвращает путь к видеофайлу."""
        import yt_dlp

        attachments_dir = output_dir / "attachments"

        opts = {
            "format": self.cfg.ytdlp_format,
            "outtmpl": str(attachments_dir / "%(title).80s.%(ext)s"),
            "writethumbnail": True,
            "no_playlist": True,
            "quiet": True,
            "noprogress": True,
        }
        if self.cfg.ytdlp_cookies_file:
            opts["cookiefile"] = self.cfg.ytdlp_cookies_file

        # Postprocessors: convert thumbnail to jpg
        opts["postprocessors"] = [
            {
                "key": "FFmpegThumbnailsConvertor",
                "format": "jpg",
            },
        ]

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        # Находим скачанный видеофайл (самый большой файл в attachments/)
        video_path = None
        max_size = 0
        for f in attachments_dir.iterdir():
            if f.is_file() and f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                if f.stat().st_size > max_size:
                    max_size = f.stat().st_size
                    video_path = f

        if not video_path:
            raise ExternalCollectorError("Видеофайл не найден после скачивания.", step="download")

        logger.info("  Видео скачано: %s (%d МБ)", video_path.name, max_size // (1024 * 1024))
        return video_path

    # ── Transcription ──────────────────────────────────────────

    def _transcribe_video(
        self, video_path: Path, collected_dir: Path, job_id: str
    ) -> tuple:
        """Транскрибирует видео. Возвращает (language, word_count)."""
        from app.pipeline.transcriber import Transcriber

        transcriber = Transcriber(self.cfg)
        transcript = transcriber.transcribe(str(video_path), job_id)

        # Сохраняем transcript.txt
        formatted = transcript.format_with_timestamps()
        (collected_dir / "transcript.txt").write_text(formatted, encoding="utf-8")
        logger.info("  Транскрипт: %d слов, язык: %s", transcript.word_count, transcript.language)

        # Сохраняем в БД
        try:
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
        except Exception:
            logger.debug("Транскрипт уже в БД, пропускаю.")

        return transcript.language, transcript.word_count

    # ── Metadata builders ──────────────────────────────────────

    def _build_meta(
        self,
        info: dict,
        link: ExternalLink,
        downloaded_files: list,
        has_transcript: bool,
        transcript_language: Optional[str],
        transcript_word_count: int,
    ) -> dict:
        """Формирует meta.json."""
        return {
            "source": link.source,
            "video_id": link.video_id,
            "url": link.raw_url,
            "title": info.get("title", ""),
            "uploader": info.get("uploader") or info.get("channel", ""),
            "upload_date": info.get("upload_date", ""),
            "duration_sec": info.get("duration") or 0,
            "description_length": len(info.get("description") or ""),
            "has_transcript": has_transcript,
            "transcript_language": transcript_language,
            "transcript_word_count": transcript_word_count,
            "files": [
                {
                    "type": f["type"],
                    "filename": f["filename"],
                    "mime_type": f["mime_type"],
                    "file_size_bytes": f["file_size_bytes"],
                    "path": f["path"],
                }
                for f in downloaded_files
            ],
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }

    def _build_manifest(
        self,
        collected_dir: Path,
        downloaded_files: list,
        has_transcript: bool,
        has_description: bool,
    ) -> dict:
        """Формирует manifest.json (индекс артефактов)."""
        artifacts = []
        total_size = 0

        # description.txt
        desc_path = collected_dir / "description.txt"
        if has_description and desc_path.exists():
            size = desc_path.stat().st_size
            artifacts.append({
                "type": "description",
                "file": "description.txt",
                "size_bytes": size,
            })
            total_size += size

        # transcript.txt
        transcript_path = collected_dir / "transcript.txt"
        if has_transcript and transcript_path.exists():
            size = transcript_path.stat().st_size
            artifacts.append({
                "type": "transcript",
                "file": "transcript.txt",
                "size_bytes": size,
            })
            total_size += size

        # attachments
        for f in downloaded_files:
            artifacts.append({
                "type": "attachment",
                "file": f["path"],
                "mime_type": f["mime_type"],
                "size_bytes": f["file_size_bytes"],
            })
            total_size += f["file_size_bytes"]

        return {
            "version": 1,
            "source": "external",
            "artifacts": artifacts,
            "total_size_bytes": total_size,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
