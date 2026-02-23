"""
Классификация Telegram-сообщений для выбора пайплайна обработки.

MessageType определяет маршрут:
  AUDIO_VIDEO       → Orchestrator (скачивание + транскрипция + PDF)
  TEXT_ONLY          → IngestOrchestrator (сохранение текста)
  TEXT_WITH_IMAGES   → IngestOrchestrator (текст + изображения)
  TEXT_WITH_DOCS     → IngestOrchestrator (текст + документы)
"""
import logging
from enum import Enum
from typing import Optional

from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
    PeerChannel,
)

from app.utils.url_parser import TelegramLink
from app.utils.async_utils import run_sync

logger = logging.getLogger("tgassistant.classifier")


class MessageType(Enum):
    AUDIO_VIDEO = "audio_video"
    TEXT_ONLY = "text_only"
    TEXT_WITH_IMAGES = "text_with_images"
    TEXT_WITH_DOCS = "text_with_docs"


# MIME-типы, которые считаются аудио/видео
_AV_MIME_PREFIXES = ("video/", "audio/")


def _is_audio_video_media(media) -> bool:
    """Проверяет, является ли медиа аудио/видео (включая голосовые и видео-заметки)."""
    if not isinstance(media, MessageMediaDocument):
        return False
    if not media.document:
        return False

    doc = media.document
    mime = doc.mime_type or ""

    # Проверяем атрибуты: видео или аудио
    for attr in doc.attributes:
        if isinstance(attr, (DocumentAttributeVideo, DocumentAttributeAudio)):
            return True

    # Проверяем MIME-тип
    if any(mime.startswith(prefix) for prefix in _AV_MIME_PREFIXES):
        return True

    return False


def _is_image(media) -> bool:
    """Проверяет, является ли медиа изображением."""
    if isinstance(media, MessageMediaPhoto):
        return True
    # Документ с image/* MIME (например, SVG или WebP отправленные как файл)
    if isinstance(media, MessageMediaDocument) and media.document:
        mime = media.document.mime_type or ""
        if mime.startswith("image/"):
            return True
    return False


def _is_document(media) -> bool:
    """Проверяет, является ли медиа документом (не аудио/видео и не фото)."""
    if not isinstance(media, MessageMediaDocument):
        return False
    if not media.document:
        return False
    # Исключаем аудио/видео и изображения
    if _is_audio_video_media(media):
        return False
    mime = media.document.mime_type or ""
    if mime.startswith("image/"):
        return False
    return True


async def _get_message(client: TelegramClient, link: TelegramLink):
    """Получает сообщение из Telegram по ссылке."""
    # Определяем сущность канала
    if link.channel_username:
        peer = await client.get_entity(link.channel_username)
    else:
        peer = await client.get_entity(PeerChannel(link.chat_id))

    messages = await client.get_messages(peer, ids=link.msg_id)
    message = messages if not isinstance(messages, list) else (messages[0] if messages else None)
    return message


def classify(client: TelegramClient, link: TelegramLink) -> MessageType:
    """
    Определяет тип сообщения для маршрутизации пайплайна.

    Приоритет: если есть аудио/видео — AUDIO_VIDEO (даже при наличии текста).

    Returns:
        MessageType
    """
    message = run_sync(_get_message(client, link))

    if message is None:
        raise ValueError(f"Сообщение {link.msg_id} не найдено или удалено.")

    has_text = bool(message.text)
    media = message.media

    # Нет текста и нет медиа — нечего обрабатывать
    if not has_text and not media:
        raise ValueError("Пустое сообщение: нет текста и нет медиа.")

    # Есть аудио/видео → всегда в медиа-пайплайн
    if media and _is_audio_video_media(media):
        return MessageType.AUDIO_VIDEO

    # Изображение
    if media and _is_image(media):
        return MessageType.TEXT_WITH_IMAGES

    # Документ (не аудио/видео, не изображение)
    if media and _is_document(media):
        return MessageType.TEXT_WITH_DOCS

    # Только текст (или медиа, которое мы не распознали — трактуем как текст)
    if has_text:
        return MessageType.TEXT_ONLY

    # Медиа без текста, которое не аудио/видео/фото/документ — ошибка
    raise ValueError(
        "Неподдерживаемый тип сообщения: нет текста, медиа не распознано."
    )
