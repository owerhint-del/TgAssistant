"""
Ingest-оркестратор: сохраняет текст/изображения/документы из Telegram 1:1.
Без LLM, без рерайта — только верное сохранение оригинала.

Структура вывода:
  <output_dir>/wiki/<chat_id>/<msg_id>/
    ├── text.txt        # текст сообщения (verbatim)
    ├── images/         # скачанные изображения
    ├── docs/           # скачанные документы
    └── meta.json       # метаданные сообщения
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

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
from app.pipeline.downloader import AccessDeniedError, MediaNotFoundError

logger = logging.getLogger("tgassistant.ingest")


class IngestError(Exception):
    """Ошибка ingest-пайплайна."""
    def __init__(self, message: str, step: str = "ingest"):
        super().__init__(message)
        self.step = step


class IngestOrchestrator:
    def __init__(self, cfg: Config, db: Database, progress_cb: Optional[Callable] = None):
        self.cfg = cfg
        self.db = db
        self._progress_cb = progress_cb

    def _wiki_dir(self, link: TelegramLink) -> Path:
        """Путь к папке wiki для данного сообщения."""
        # Для публичных каналов используем username, для приватных — chat_id
        channel_id = link.channel_username or str(link.chat_id)
        return Path(self.cfg.output_dir) / "wiki" / channel_id / str(link.msg_id)

    def run(
        self,
        job_id: str,
        link: TelegramLink,
        client: TelegramClient,
        from_start: bool = False,
    ) -> dict:
        """
        Сохраняет содержимое сообщения 1:1 в wiki-структуру.

        Returns:
            dict с ключом 'wiki_dir' — путь к сохранённой папке
        """
        def _notify(status: str, **extra):
            if self._progress_cb:
                try:
                    self._progress_cb(job_id, status, **extra)
                except Exception:
                    pass

        wiki_dir = self._wiki_dir(link)

        # Проверяем, не завершён ли уже ingest (resume)
        if not from_start and (wiki_dir / "meta.json").exists():
            logger.info("Ingest уже выполнен, используем кэш: %s", wiki_dir)
            self.db.update_job_status(job_id, "done")
            # Сохраняем export запись если её нет
            exports = self.db.get_exports(job_id)
            if not any(e["export_type"] == "ingest_wiki" for e in exports):
                self.db.save_export(job_id, "ingest_wiki", str(wiki_dir))
            return {"wiki_dir": str(wiki_dir)}

        self.db.update_job_status(job_id, "collecting")
        _notify("collecting")
        logger.info("Ingest: сохраняю сообщение в %s", wiki_dir)

        # Получаем сообщение
        try:
            message = run_sync(self._fetch_message(client, link))
        except (AccessDeniedError, MediaNotFoundError):
            raise
        except Exception as e:
            raise IngestError(str(e), step="fetch")

        if message is None:
            raise MediaNotFoundError(
                f"Сообщение {link.msg_id} не найдено или удалено."
            )

        # Создаём структуру директорий
        wiki_dir.mkdir(parents=True, exist_ok=True)

        # Шаг A: Сохраняем текст
        if message.text:
            text_path = wiki_dir / "text.txt"
            text_path.write_text(message.text, encoding="utf-8")
            logger.info("  Текст сохранён: %s (%d символов)", text_path, len(message.text))

        # Шаг B: Скачиваем медиа (изображения и документы)
        downloaded_files = []
        if message.media:
            try:
                downloaded_files = run_sync(
                    self._download_media(client, message, wiki_dir)
                )
            except Exception as e:
                raise IngestError(f"Ошибка скачивания медиа: {e}", step="download_media")

        # Шаг C: Записываем meta.json
        meta = self._build_meta(message, link, downloaded_files)
        meta_path = wiki_dir / "meta.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("  meta.json записан: %s", meta_path)

        # Сохраняем export запись в БД
        self.db.save_export(job_id, "ingest_wiki", str(wiki_dir))

        # Done
        self.db.update_job_status(job_id, "done")
        _notify("done")
        logger.info("Ingest завершён: %s", wiki_dir)

        return {"wiki_dir": str(wiki_dir)}

    async def _fetch_message(self, client: TelegramClient, link: TelegramLink):
        """Получает сообщение из Telegram."""
        try:
            if link.channel_username:
                peer = await client.get_entity(link.channel_username)
            else:
                peer = await client.get_entity(PeerChannel(link.chat_id))
        except (ChannelPrivateError, ChatAdminRequiredError):
            raise AccessDeniedError(
                f"Нет доступа к каналу. Убедись, что твой аккаунт состоит в этом канале."
            )
        except ValueError:
            raise AccessDeniedError(
                f"Канал не найден. Проверь ссылку."
            )

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

    async def _download_media(self, client: TelegramClient, message, wiki_dir: Path) -> list:
        """Скачивает медиа из сообщения в соответствующую подпапку."""
        downloaded = []
        media = message.media

        if isinstance(media, MessageMediaPhoto):
            # Фотография
            images_dir = wiki_dir / "images"
            images_dir.mkdir(exist_ok=True)
            path = await client.download_media(message, file=str(images_dir) + "/")
            if path:
                downloaded.append({
                    "type": "image",
                    "filename": os.path.basename(path),
                    "path": str(Path(path).relative_to(wiki_dir)),
                })
                logger.info("  Изображение скачано: %s", path)

        elif isinstance(media, MessageMediaDocument) and media.document:
            doc = media.document
            mime = doc.mime_type or ""

            if mime.startswith("image/"):
                # Изображение, отправленное как документ
                images_dir = wiki_dir / "images"
                images_dir.mkdir(exist_ok=True)
                path = await client.download_media(message, file=str(images_dir) + "/")
            else:
                # Документ (PDF, Word, и т.д.)
                docs_dir = wiki_dir / "docs"
                docs_dir.mkdir(exist_ok=True)
                path = await client.download_media(message, file=str(docs_dir) + "/")

            if path:
                file_type = "image" if mime.startswith("image/") else "document"
                downloaded.append({
                    "type": file_type,
                    "filename": os.path.basename(path),
                    "mime_type": mime,
                    "file_size_bytes": doc.size,
                    "path": str(Path(path).relative_to(wiki_dir)),
                })
                logger.info("  %s скачан: %s", file_type.capitalize(), path)

        return downloaded

    def _build_meta(self, message, link: TelegramLink, downloaded_files: list) -> dict:
        """Формирует meta.json с метаданными сообщения."""
        meta = {
            "msg_id": link.msg_id,
            "chat_id": link.chat_id,
            "channel_username": link.channel_username,
            "url": link.raw_url,
            "date": message.date.isoformat() if message.date else None,
            "has_text": bool(message.text),
            "text_length": len(message.text) if message.text else 0,
            "files": downloaded_files,
            "ingested_at": datetime.utcnow().isoformat(),
        }

        # Информация о пересылке
        if message.forward:
            fwd = message.forward
            meta["forward"] = {
                "from_id": getattr(fwd, "from_id", None),
                "from_name": getattr(fwd, "from_name", None),
                "date": fwd.date.isoformat() if fwd.date else None,
                "channel_post": getattr(fwd, "channel_post", None),
            }
            # Конвертируем from_id в сериализуемый формат
            if meta["forward"]["from_id"] is not None:
                fid = meta["forward"]["from_id"]
                if hasattr(fid, "channel_id"):
                    meta["forward"]["from_id"] = fid.channel_id
                elif hasattr(fid, "user_id"):
                    meta["forward"]["from_id"] = fid.user_id

        return meta
