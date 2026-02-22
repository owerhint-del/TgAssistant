"""
Политика очистки временных файлов.

Retention policy:
  - Успех          → удалить temp media немедленно
  - Падение DOWNLOAD → оставить media (для retry)
  - Падение TRANSCRIBE → оставить media (для retry), удалить .wav
  - Падение SUMMARIZE+ → удалить media (transcript уже в DB)
  - Orphans (нет в DB, старше N часов) → удалить
"""
import os
import time
import logging
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
