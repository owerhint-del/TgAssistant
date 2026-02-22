"""
Транскрибация аудио/видео через faster-whisper.
Конвертация медиа → WAV через ffmpeg.
"""
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from app.config import Config

logger = logging.getLogger("tgassistant.transcriber")

# Порог уверенности: ниже — считаем неразборчивым
UNRECOGNIZED_THRESHOLD = -1.0
UNRECOGNIZED_LABEL = "[неразборчиво]"
PAUSE_THRESHOLD_SEC = 2.0  # пауза в секундах — начало нового абзаца


@dataclass
class Segment:
    start: float
    end: float
    text: str
    avg_logprob: float = 0.0


@dataclass
class TranscriptResult:
    segments: List[Segment]
    language: str
    model_used: str
    duration_sec: Optional[float]
    full_text: str = ""
    word_count: int = 0
    unrecognized_count: int = 0

    def __post_init__(self):
        if not self.full_text:
            self.full_text = "\n".join(s.text for s in self.segments)
        if not self.word_count:
            self.word_count = len(self.full_text.split())

    def to_segments_json(self) -> list:
        return [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "avg_logprob": s.avg_logprob,
            }
            for s in self.segments
        ]

    def format_with_timestamps(self) -> str:
        """Форматирует транскрипт с таймкодами [ЧЧ:ММ:СС] по абзацам."""
        if not self.segments:
            return ""

        lines = []
        paragraph_segments = []
        last_end = 0.0

        for seg in self.segments:
            gap = seg.start - last_end
            if paragraph_segments and gap > PAUSE_THRESHOLD_SEC:
                # Завершаем текущий абзац
                lines.append(_format_paragraph(paragraph_segments))
                lines.append("")  # пустая строка между абзацами
                paragraph_segments = []
            paragraph_segments.append(seg)
            last_end = seg.end

        if paragraph_segments:
            lines.append(_format_paragraph(paragraph_segments))

        return "\n".join(lines)


def _format_paragraph(segments: List[Segment]) -> str:
    timestamp = _fmt_time(segments[0].start)
    text = " ".join(s.text.strip() for s in segments)
    return f"[{timestamp}]\n{text}"


def _fmt_time(seconds: float) -> str:
    """3661.5 → '01:01:01'"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class Transcriber:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._model = None

    def _get_model(self):
        """Ленивая загрузка модели Whisper (первый вызов = скачивание ~3 ГБ)."""
        if self._model is None:
            from faster_whisper import WhisperModel
            logger.info(
                "Загружаю модель Whisper '%s' (первый запуск может занять 5–15 минут)...",
                self.cfg.whisper_model,
            )
            self._model = WhisperModel(
                self.cfg.whisper_model,
                device=self.cfg.whisper_device,
                compute_type=self.cfg.whisper_compute_type,
            )
            logger.info("Модель загружена.")
        return self._model

    def _extract_audio(self, media_path: str, job_id: str) -> str:
        """Конвертирует медиафайл в 16kHz mono WAV для Whisper."""
        wav_path = os.path.join(self.cfg.temp_dir, f"{job_id}.wav")
        if Path(wav_path).exists():
            logger.debug("WAV уже существует, пропускаю конвертацию.")
            return wav_path

        logger.info("Конвертирую аудио в WAV...")
        cmd = [
            "ffmpeg", "-y",
            "-i", media_path,
            "-ar", "16000",    # 16kHz — оптимально для Whisper
            "-ac", "1",        # mono
            "-vn",             # без видео
            wav_path,
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"ffmpeg завершился с ошибкой:\n{err}\n\n"
                "Убедись что ffmpeg установлен: brew install ffmpeg"
            )
        logger.info("WAV создан: %s", wav_path)
        return wav_path

    def transcribe(self, media_path: str, job_id: str) -> TranscriptResult:
        """
        Полный цикл: медиафайл → TranscriptResult.
        """
        # 1. Конвертация в WAV
        wav_path = self._extract_audio(media_path, job_id)

        # 2. Транскрибация
        model = self._get_model()
        logger.info("Запускаю транскрибацию (это займёт время)...")

        segments_iter, info = model.transcribe(
            wav_path,
            language=self.cfg.whisper_language if self.cfg.whisper_language != "auto" else None,
            beam_size=self.cfg.whisper_beam_size,
            vad_filter=self.cfg.whisper_vad_filter,
            vad_parameters={"min_silence_duration_ms": 500},
            word_timestamps=False,
            condition_on_previous_text=True,
        )

        detected_language = info.language
        duration_sec = getattr(info, "duration", None)

        logger.info("Определён язык: %s. Транскрибирую...", detected_language)

        segments: List[Segment] = []
        unrecognized_count = 0

        for raw_seg in segments_iter:
            avg_logprob = getattr(raw_seg, "avg_logprob", 0.0)
            if avg_logprob < UNRECOGNIZED_THRESHOLD:
                text = UNRECOGNIZED_LABEL
                unrecognized_count += 1
            else:
                text = raw_seg.text.strip()
                if not text:
                    continue

            segments.append(Segment(
                start=raw_seg.start,
                end=raw_seg.end,
                text=text,
                avg_logprob=avg_logprob,
            ))

        # 3. Удаляем WAV
        try:
            Path(wav_path).unlink()
            logger.debug("WAV удалён: %s", wav_path)
        except OSError:
            pass

        word_count = sum(len(s.text.split()) for s in segments if s.text != UNRECOGNIZED_LABEL)

        logger.info(
            "Транскрибация завершена: %d сегментов, %d слов, %d [неразборчиво]",
            len(segments), word_count, unrecognized_count,
        )

        result = TranscriptResult(
            segments=segments,
            language=detected_language,
            model_used=self.cfg.whisper_model,
            duration_sec=duration_sec,
            word_count=word_count,
            unrecognized_count=unrecognized_count,
        )
        # full_text строится в __post_init__
        result.full_text = "\n".join(
            s.text for s in segments if s.text != UNRECOGNIZED_LABEL
        )

        return result
