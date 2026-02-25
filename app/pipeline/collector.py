"""
Collector-оркестратор: единый пайплайн сбора данных из Telegram-сообщений.

Собирает ВСЁ из сообщения в одну папку:
  - текст/подпись → text.txt
  - изображения, документы, аудио, видео → attachments/
  - транскрипция аудио/видео → transcript.txt
  - метаданные → meta.json
  - индекс артефактов → manifest.json (маркер завершения)

Структура вывода:
  <output_dir>/collected/<channel_id>/<msg_id>/
    ├── meta.json
    ├── text.txt           (если есть текст/подпись)
    ├── transcript.txt     (если есть аудио/видео)
    ├── attachments/       (все медиа и документы)
    └── manifest.json      (пишется ПОСЛЕДНИМ = маркер завершения)
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, List

from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    PeerChannel,
)
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    MessageIdInvalidError,
    FloodWaitError,
    MsgIdInvalidError,
)

from app.config import Config
from app.db.database import Database
from app.utils.url_parser import TelegramLink
from app.utils.async_utils import run_sync
from app.pipeline.downloader import AccessDeniedError, MediaNotFoundError, MediaLimitExceededError
from app.pipeline.classifier import _is_audio_video_media, _is_image, _is_document

logger = logging.getLogger("tgassistant.collector")

# Максимальный размер альбома в Telegram
_ALBUM_SEARCH_LIMIT = 30
_ALBUM_SEARCH_OFFSET = 15


class CollectorError(Exception):
    """Ошибка collector-пайплайна (retryable)."""
    def __init__(self, message: str, step: str = "collect"):
        super().__init__(message)
        self.step = step


class CollectorOrchestrator:
    def __init__(self, cfg: Config, db: Database, progress_cb: Optional[Callable] = None):
        self.cfg = cfg
        self.db = db
        self._progress_cb = progress_cb

    def _collected_dir(self, link: TelegramLink) -> Path:
        """Путь к папке collected для данного сообщения."""
        channel_id = link.channel_username or str(link.chat_id)
        return Path(self.cfg.output_dir) / "collected" / channel_id / str(link.msg_id)

    def run(
        self,
        job_id: str,
        link: TelegramLink,
        client: TelegramClient,
        from_start: bool = False,
    ) -> dict:
        """
        Собирает всё содержимое сообщения в единую папку.

        Returns:
            dict с ключом 'collected_dir' — путь к папке
        """
        def _notify(status: str, **extra):
            if self._progress_cb:
                try:
                    self._progress_cb(job_id, status, **extra)
                except Exception:
                    pass

        collected_dir = self._collected_dir(link)

        # ── 1. IDEMPOTENCY CHECK ──────────────────────────────
        if not from_start and (collected_dir / "manifest.json").exists():
            logger.info("Collecting уже выполнен, используем кэш: %s", collected_dir)
            self.db.update_job_status(job_id, "done")
            # Сохраняем export запись если её нет
            exports = self.db.get_exports(job_id)
            if not any(e["export_type"] == "collected" for e in exports):
                self.db.save_export(job_id, "collected", str(collected_dir))
            return {"collected_dir": str(collected_dir)}

        # ── 2. ANALYZING ──────────────────────────────────────
        self.db.update_job_status(job_id, "analyzing")
        _notify("analyzing")
        logger.info("Collector: анализирую сообщение для %s", collected_dir)

        # Получаем сообщение
        try:
            message = run_sync(self._fetch_message(client, link))
        except (AccessDeniedError, MediaNotFoundError):
            raise
        except Exception as e:
            raise CollectorError(str(e), step="fetch")

        if message is None:
            raise MediaNotFoundError(
                f"Сообщение {link.msg_id} не найдено или удалено."
            )

        # Проверяем альбом (grouped_id)
        try:
            messages = run_sync(self._fetch_album(client, link, message))
        except Exception as e:
            logger.warning("Не удалось получить альбом, используем одно сообщение: %s", e)
            messages = [message]

        # Определяем типы контента
        has_text = any(bool(m.text) for m in messages)
        has_av = any(m.media and _is_audio_video_media(m.media) for m in messages)
        has_images = any(m.media and _is_image(m.media) for m in messages)
        has_docs = any(m.media and _is_document(m.media) for m in messages)

        # Определяем message_type для meta
        if has_av:
            message_type = "audio_video"
        elif has_images and has_docs:
            message_type = "mixed"
        elif has_images:
            message_type = "text_with_images"
        elif has_docs:
            message_type = "text_with_docs"
        elif has_text:
            message_type = "text_only"
        else:
            raise MediaNotFoundError("Пустое сообщение: нет текста и нет медиа.")

        # Создаём структуру директорий
        collected_dir.mkdir(parents=True, exist_ok=True)

        # ── 3. COLLECTING ─────────────────────────────────────
        self.db.update_job_status(job_id, "collecting")
        _notify("collecting")
        logger.info("Collector: собираю содержимое...")

        # Сохраняем текст
        if has_text:
            texts = []
            for m in messages:
                if m.text:
                    texts.append(m.text)
            combined_text = "\n\n---\n\n".join(texts) if len(texts) > 1 else (texts[0] if texts else "")
            text_path = collected_dir / "text.txt"
            text_path.write_text(combined_text, encoding="utf-8")
            logger.info("  Текст сохранён: %d символов", len(combined_text))

        # Скачиваем все медиа в attachments/
        downloaded_files = []
        for m in messages:
            if m.media:
                try:
                    files = run_sync(
                        self._download_media(client, m, collected_dir)
                    )
                    downloaded_files.extend(files)
                except MediaLimitExceededError:
                    raise
                except Exception as e:
                    raise CollectorError(f"Ошибка скачивания медиа: {e}", step="download_media")

        # ── 4. TRANSCRIBING (условно) ─────────────────────────
        transcript_text = None
        transcript_language = None
        transcript_word_count = 0

        av_files = [f for f in downloaded_files if f.get("is_av")]
        if av_files:
            self.db.update_job_status(job_id, "transcribing")
            _notify("transcribing")
            logger.info("Collector: транскрибирую %d аудио/видео файл(ов)...", len(av_files))

            try:
                transcript_text, transcript_language, transcript_word_count = self._transcribe_files(
                    av_files, collected_dir, job_id
                )
            except Exception as e:
                raise CollectorError(f"Ошибка транскрипции: {e}", step="transcribe")

        # ── 5. SAVING ─────────────────────────────────────────
        self.db.update_job_status(job_id, "saving")
        _notify("saving")
        logger.info("Collector: записываю метаданные...")

        # meta.json
        meta = self._build_meta(
            messages=messages,
            link=link,
            message_type=message_type,
            downloaded_files=downloaded_files,
            has_text=has_text,
            transcript_language=transcript_language,
            transcript_word_count=transcript_word_count,
        )
        meta_path = collected_dir / "meta.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # manifest.json (пишется ПОСЛЕДНИМ = маркер завершения)
        manifest = self._build_manifest(
            collected_dir=collected_dir,
            message_type=message_type,
            downloaded_files=downloaded_files,
            has_text=has_text,
            has_transcript=transcript_text is not None,
        )
        manifest_path = collected_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Сохраняем export запись в БД
        self.db.save_export(job_id, "collected", str(collected_dir))

        # Done
        self.db.update_job_status(job_id, "done")
        _notify("done")
        logger.info("Collector завершён: %s", collected_dir)

        return {"collected_dir": str(collected_dir)}

    # ── Telegram helpers ──────────────────────────────────────

    async def _fetch_message(self, client: TelegramClient, link: TelegramLink):
        """Получает сообщение из Telegram."""
        try:
            if link.channel_username:
                peer = await client.get_entity(link.channel_username)
            else:
                peer = await client.get_entity(PeerChannel(link.chat_id))
        except (ChannelPrivateError, ChatAdminRequiredError):
            raise AccessDeniedError(
                "Нет доступа к каналу. Убедись, что твой аккаунт состоит в этом канале."
            )
        except ValueError:
            raise AccessDeniedError("Канал не найден. Проверь ссылку.")

        try:
            messages = await client.get_messages(peer, ids=link.msg_id)
        except (MessageIdInvalidError, MsgIdInvalidError):
            raise MediaNotFoundError(
                f"Сообщение {link.msg_id} не найдено или удалено."
            )
        except FloodWaitError as e:
            logger.warning("FloodWait: ждём %d сек...", e.seconds)
            await asyncio.sleep(e.seconds + 10)
            messages = await client.get_messages(peer, ids=link.msg_id)

        message = messages if not isinstance(messages, list) else (messages[0] if messages else None)
        return message

    async def _fetch_album(self, client: TelegramClient, link: TelegramLink, message):
        """Получает все сообщения альбома по grouped_id."""
        if not getattr(message, "grouped_id", None):
            return [message]

        # Определяем peer
        if link.channel_username:
            peer = await client.get_entity(link.channel_username)
        else:
            peer = await client.get_entity(PeerChannel(link.chat_id))

        # Альбомы — это до 10 последовательных сообщений
        batch = await client.get_messages(
            peer, limit=_ALBUM_SEARCH_LIMIT, min_id=message.id - _ALBUM_SEARCH_OFFSET
        )
        album = sorted(
            [m for m in batch if m and getattr(m, "grouped_id", None) == message.grouped_id],
            key=lambda m: m.id,
        )
        return album or [message]

    # ── Download helpers ──────────────────────────────────────

    async def _download_media(self, client: TelegramClient, message, collected_dir: Path) -> list:
        """Скачивает медиа из сообщения в attachments/."""
        downloaded = []
        media = message.media
        if not media:
            return downloaded

        attachments_dir = collected_dir / "attachments"
        attachments_dir.mkdir(exist_ok=True)

        is_av = _is_audio_video_media(media)

        # Проверка размера для документов
        if isinstance(media, MessageMediaDocument) and media.document:
            doc = media.document
            file_size_bytes = doc.size
            max_bytes = self.cfg.max_file_mb * 1024 * 1024
            if file_size_bytes and file_size_bytes > max_bytes:
                size_mb = file_size_bytes / 1024 / 1024
                raise MediaLimitExceededError(
                    f"Файл слишком большой: {size_mb:.0f} МБ "
                    f"(максимум {self.cfg.max_file_mb} МБ)."
                )

        path = await client.download_media(message, file=str(attachments_dir) + "/")
        if path:
            filename = os.path.basename(path)
            rel_path = f"attachments/{filename}"
            file_size = Path(path).stat().st_size

            # Определяем тип и mime
            file_type = "attachment"
            mime_type = ""

            if isinstance(media, MessageMediaPhoto):
                file_type = "image"
                mime_type = "image/jpeg"
            elif isinstance(media, MessageMediaDocument) and media.document:
                doc = media.document
                mime_type = doc.mime_type or ""
                if is_av:
                    file_type = "audio_video"
                elif mime_type.startswith("image/"):
                    file_type = "image"
                else:
                    file_type = "document"

            entry = {
                "type": file_type,
                "filename": filename,
                "mime_type": mime_type,
                "file_size_bytes": file_size,
                "path": rel_path,
                "is_av": is_av,
            }
            downloaded.append(entry)
            logger.info("  %s скачан: %s (%d байт)", file_type, filename, file_size)

        return downloaded

    # ── Transcription ─────────────────────────────────────────

    def _transcribe_files(
        self, av_files: list, collected_dir: Path, job_id: str
    ) -> tuple:
        """Транскрибирует аудио/видео файлы. Возвращает (text, language, word_count)."""
        from app.pipeline.transcriber import Transcriber

        transcriber = Transcriber(self.cfg)
        all_texts = []
        language = None
        total_words = 0

        for av in av_files:
            media_path = str(collected_dir / av["path"])
            transcript = transcriber.transcribe(media_path, job_id)

            formatted = transcript.format_with_timestamps()
            all_texts.append(formatted)
            language = transcript.language
            total_words += transcript.word_count

            # Сохраняем транскрипт в БД (для будущего использования)
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
                # Может быть UNIQUE constraint если уже есть — не критично
                logger.debug("Транскрипт уже в БД, пропускаю.")

        # Записываем transcript.txt
        combined = "\n\n".join(all_texts)
        transcript_path = collected_dir / "transcript.txt"
        transcript_path.write_text(combined, encoding="utf-8")
        logger.info("  Транскрипт сохранён: %d слов, язык: %s", total_words, language)

        return combined, language, total_words

    # ── Metadata builders ─────────────────────────────────────

    def _build_meta(
        self,
        messages: list,
        link: TelegramLink,
        message_type: str,
        downloaded_files: list,
        has_text: bool,
        transcript_language: Optional[str],
        transcript_word_count: int,
    ) -> dict:
        """Формирует meta.json."""
        primary = messages[0]
        text_length = 0
        if has_text:
            text_length = sum(len(m.text) for m in messages if m.text)

        meta = {
            "msg_id": link.msg_id,
            "chat_id": link.chat_id,
            "channel_username": link.channel_username,
            "url": link.raw_url,
            "date": primary.date.isoformat() if primary.date else None,
            "message_type": message_type,
            "has_text": has_text,
            "text_length": text_length,
            "has_transcript": transcript_language is not None,
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

        # Альбом
        if len(messages) > 1:
            meta["album"] = {
                "grouped_id": getattr(primary, "grouped_id", None),
                "message_ids": [m.id for m in messages],
            }

        # Информация о пересылке
        if primary.forward:
            fwd = primary.forward
            meta["forward"] = {
                "from_name": getattr(fwd, "from_name", None),
                "date": fwd.date.isoformat() if fwd.date else None,
                "channel_post": getattr(fwd, "channel_post", None),
            }
            from_id = getattr(fwd, "from_id", None)
            if from_id is not None:
                if hasattr(from_id, "channel_id"):
                    meta["forward"]["from_id"] = from_id.channel_id
                elif hasattr(from_id, "user_id"):
                    meta["forward"]["from_id"] = from_id.user_id

        return meta

    def _build_manifest(
        self,
        collected_dir: Path,
        message_type: str,
        downloaded_files: list,
        has_text: bool,
        has_transcript: bool,
    ) -> dict:
        """Формирует manifest.json (индекс артефактов)."""
        artifacts = []
        total_size = 0

        # text.txt
        text_path = collected_dir / "text.txt"
        if has_text and text_path.exists():
            size = text_path.stat().st_size
            artifacts.append({
                "type": "text",
                "file": "text.txt",
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
            "message_type": message_type,
            "artifacts": artifacts,
            "total_size_bytes": total_size,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
