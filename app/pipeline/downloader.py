"""
Скачивание медиафайла из приватного Telegram-канала через Telethon.
"""
import asyncio
import os
import logging
from pathlib import Path
from typing import Optional, Tuple

from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
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
from app.utils.url_parser import TelegramLink

logger = logging.getLogger("tgassistant.downloader")

# Поддерживаемые типы медиа
SUPPORTED_MIME_PREFIXES = ("video/", "audio/")
SUPPORTED_MIME_EXACT = ("application/octet-stream",)


class DownloadError(Exception):
    """Ошибка скачивания — не требует retry."""


class MediaNotFoundError(DownloadError):
    pass


class AccessDeniedError(DownloadError):
    pass


class UnsupportedMediaError(DownloadError):
    pass


class MediaLimitExceededError(DownloadError):
    pass


def _make_temp_path(temp_dir: str, link: TelegramLink, ext: str) -> str:
    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    channel_id = link.channel_username or str(link.chat_id)
    return os.path.join(temp_dir, f"media_{channel_id}_{link.msg_id}{ext}")


def _detect_media_info(message) -> Tuple[str, str, Optional[str], Optional[float]]:
    """
    Определяет тип медиа, расширение, MIME-тип и длительность.
    Возвращает (asset_type, extension, mime_type, duration_sec).
    """
    media = message.media

    # Голосовое сообщение
    if hasattr(media, "document") and media.document:
        doc = media.document
        mime = doc.mime_type or ""
        duration = None

        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                duration = attr.duration
                if attr.round_message:
                    return "video_note", ".mp4", mime, duration
                return "video", ".mp4", mime, duration
            if isinstance(attr, DocumentAttributeAudio):
                duration = attr.duration
                if attr.voice:
                    return "voice", ".ogg", mime, duration
                return "audio", ".mp3", mime, duration

        # Документ с видео-mime
        if mime.startswith("video/"):
            return "video", ".mp4", mime, duration
        if mime.startswith("audio/"):
            return "audio", ".mp3", mime, duration

        raise UnsupportedMediaError(
            f"Неподдерживаемый тип медиа: {mime}\n"
            "Поддерживается: video, audio, voice, video-note"
        )

    raise UnsupportedMediaError(
        "В сообщении нет медиафайла (видео, аудио или голосового сообщения)."
    )


class TelegramDownloader:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def download(
        self,
        client: TelegramClient,
        link: TelegramLink,
        progress_cb=None,
    ) -> Tuple[str, str, str, Optional[float], Optional[int]]:
        """
        Скачивает медиафайл из указанного сообщения.

        Returns:
            (temp_path, asset_type, mime_type, duration_sec, file_size_bytes)
        """
        # Получаем сущность канала (по username или chat_id)
        try:
            if link.channel_username:
                peer = await client.get_entity(link.channel_username)
            else:
                peer = await client.get_entity(PeerChannel(link.chat_id))
        except (ChannelPrivateError, ChatAdminRequiredError):
            channel_ref = link.channel_username or link.chat_id
            raise AccessDeniedError(
                f"Нет доступа к каналу {channel_ref}.\n"
                "Убедись, что твой аккаунт состоит в этом канале."
            )
        except ValueError:
            channel_ref = link.channel_username or link.chat_id
            raise AccessDeniedError(
                f"Канал {channel_ref} не найден. Проверь ссылку."
            )

        # Получаем сообщение
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

        if message is None:
            raise MediaNotFoundError(
                f"Сообщение {link.msg_id} не найдено или удалено."
            )

        if not message.media:
            raise UnsupportedMediaError(
                "В сообщении нет медиафайла (видео, аудио или голосового сообщения)."
            )

        # Определяем тип и расширение
        asset_type, ext, mime_type, duration_sec = _detect_media_info(message)

        # Проверка размера
        file_size_bytes = None
        if hasattr(message.media, "document") and message.media.document:
            file_size_bytes = message.media.document.size
            max_bytes = self.cfg.max_file_mb * 1024 * 1024
            if file_size_bytes and file_size_bytes > max_bytes:
                size_mb = file_size_bytes / 1024 / 1024
                raise MediaLimitExceededError(
                    f"Файл слишком большой: {size_mb:.0f} МБ "
                    f"(максимум {self.cfg.max_file_mb} МБ).\n"
                    "Измени MAX_FILE_MB в .env чтобы разрешить больший размер."
                )

        # Проверка длительности
        if duration_sec and duration_sec > self.cfg.max_duration_sec:
            dur_h = duration_sec / 3600
            max_h = self.cfg.max_duration_sec / 3600
            raise MediaLimitExceededError(
                f"Видео слишком длинное: {dur_h:.1f} ч "
                f"(максимум {max_h:.1f} ч).\n"
                "Измени MAX_DURATION_SEC в .env чтобы разрешить большую длительность."
            )

        temp_path = _make_temp_path(self.cfg.temp_dir, link, ext)

        # Если файл уже скачан (resume после сбоя)
        if Path(temp_path).exists():
            existing_size = Path(temp_path).stat().st_size
            if file_size_bytes and abs(existing_size - file_size_bytes) < 1024:
                logger.info("Файл уже скачан (%d байт), использую кэш.", existing_size)
                return temp_path, asset_type, mime_type or "", duration_sec, file_size_bytes

        logger.info(
            "Скачиваю %s из канала %d, сообщение %d...",
            asset_type, link.chat_id, link.msg_id,
        )

        def _progress(current, total):
            if total and total > 0:
                pct = current / total * 100
                print(f"\r  Скачивание: {pct:.1f}% ({current // 1024 // 1024} МБ)", end="", flush=True)
            if progress_cb:
                progress_cb(current, total)

        downloaded_path = await client.download_media(
            message,
            file=temp_path,
            progress_callback=_progress,
        )
        print()  # новая строка после прогресс-бара

        if not downloaded_path:
            raise DownloadError("Скачивание завершилось, но файл не создан.")

        actual_size = Path(downloaded_path).stat().st_size
        logger.info("Скачано: %s (%.1f МБ)", downloaded_path, actual_size / 1024 / 1024)

        return str(downloaded_path), asset_type, mime_type or "", duration_sec, actual_size
