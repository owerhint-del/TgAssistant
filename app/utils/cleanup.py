"""
Политика очистки временных файлов и медиа.

Retention policy (temp):
  - Успех          → удалить temp media немедленно
  - Падение DOWNLOAD → оставить media (для retry)
  - Падение TRANSCRIBE → оставить media (для retry), удалить .wav
  - Падение SUMMARIZE+ → удалить media (transcript уже в DB)
  - Orphans (нет в DB, старше N часов) → удалить

Media cleanup (collected/):
  - Удаляет видео/аудио файлы старше N дней
  - Сохраняет text.txt, transcript.txt, meta.json, manifest.json, изображения
"""
import os
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("tgassistant.cleanup")


def delete_file(path: Optional[str], reason: str = "") -> bool:
    """Безопасно удаляет файл. Возвращает True если удалён."""
    if not path:
        return False
    p = Path(path)
    if p.exists():
        try:
            p.unlink()
            logger.debug("Удалён temp файл: %s [%s]", path, reason)
            return True
        except OSError as e:
            logger.warning("Не удалось удалить %s: %s", path, e)
    return False


def cleanup_after_success(media_path: Optional[str]) -> None:
    """Вызывается после успешного завершения пайплайна."""
    delete_file(media_path, reason="pipeline done")


def cleanup_wav(wav_path: Optional[str]) -> None:
    """Удаляет WAV-файл после транскрибации."""
    delete_file(wav_path, reason="transcription done")


def cleanup_orphans(temp_dir: str, retention_hours: int = 24) -> int:
    """
    Удаляет файлы в temp_dir, которые старше retention_hours.
    Запускается при старте приложения.
    Возвращает количество удалённых файлов.
    """
    temp = Path(temp_dir)
    if not temp.exists():
        return 0

    cutoff = time.time() - (retention_hours * 3600)
    deleted = 0

    for f in temp.iterdir():
        if f.name == ".gitkeep":
            continue
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                logger.info("Удалён устаревший temp файл: %s", f.name)
                deleted += 1
            except OSError as e:
                logger.warning("Не удалось удалить orphan %s: %s", f, e)

    if deleted:
        logger.info("Очистка temp: удалено %d устаревших файлов", deleted)
    return deleted


# ─── Media cleanup ──────────────────────────────────────────

# Видео и аудио расширения для удаления
_MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v",
    ".wav", ".ogg", ".mp3", ".aac", ".flac", ".m4a", ".opus", ".wma",
})


@dataclass
class CleanupResult:
    """Результат очистки медиа."""
    files_deleted: int = 0
    bytes_freed: int = 0
    files_skipped: int = 0
    errors: int = 0


def cleanup_media(
    output_dir: str,
    older_than_days: int = 7,
    dry_run: bool = False,
) -> CleanupResult:
    """
    Удаляет видео/аудио файлы в collected/ старше older_than_days.

    Сохраняет: text.txt, transcript.txt, meta.json, manifest.json, изображения.
    Удаляет: .mp4, .mov, .wav, .mp3 и другие AV-форматы.

    Args:
        output_dir: Корневая папка вывода (cfg.output_dir).
        older_than_days: Минимальный возраст файла в днях.
        dry_run: Если True — только считает, не удаляет.

    Returns:
        CleanupResult с количеством удалённых файлов и освобождённых байт.
    """
    collected = Path(output_dir) / "collected"
    if not collected.exists():
        return CleanupResult()

    cutoff = time.time() - (older_than_days * 86400)
    result = CleanupResult()

    for f in collected.rglob("*"):
        if f.is_symlink():
            continue  # не трогаем симлинки (batch index artifacts)
        if not f.is_file():
            continue
        if f.suffix.lower() not in _MEDIA_EXTENSIONS:
            continue
        try:
            stat = f.stat()
        except OSError:
            continue
        if stat.st_mtime >= cutoff:
            result.files_skipped += 1
            continue

        size = stat.st_size
        if dry_run:
            logger.info("  [dry-run] %s (%.1f МБ)", f, size / 1_048_576)
            result.files_deleted += 1
            result.bytes_freed += size
        else:
            try:
                f.unlink()
                result.files_deleted += 1
                result.bytes_freed += size
                logger.info("Удалён: %s (%.1f МБ)", f.name, size / 1_048_576)
            except OSError as e:
                logger.warning("Не удалось удалить %s: %s", f, e)
                result.errors += 1

    return result
