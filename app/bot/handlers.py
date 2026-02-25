"""
Обработчики сообщений Telegram-бота.
"""
import asyncio
import logging
import re

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.utils.url_parser import parse_url, ExternalLink
from app.batch.note_parser import parse_note
from app.bot.messages import (
    MSG_START,
    MSG_HELP,
    MSG_PROCESSING_STARTED,
    MSG_ALREADY_DONE,
    MSG_ALREADY_PROCESSING,
    MSG_INVALID_URL,
    MSG_NOT_A_LINK,
    MSG_BATCH_HELP,
    MSG_BATCH_NO_URLS,
    MSG_BATCH_STARTED,
)

logger = logging.getLogger("tgassistant.bot.handlers")

router = Router()

# Паттерн для выделения URL из текста
URL_PATTERN = re.compile(r"https?://\S+")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(MSG_START)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(MSG_HELP)


@router.message(Command("batch"))
async def cmd_batch(message: Message, bot: Bot) -> None:
    """Пакетная обработка: /batch <текст заметки с URL-ами>."""
    batch_text = (message.text or "").removeprefix("/batch").strip()

    if not batch_text:
        await message.answer(MSG_BATCH_HELP)
        return

    note = parse_note(batch_text)
    if note.valid_count == 0:
        await message.answer(MSG_BATCH_NO_URLS)
        return

    status_msg = await message.answer(
        MSG_BATCH_STARTED.format(count=note.valid_count, topic=note.topic)
    )

    loop = asyncio.get_running_loop()
    runner = bot._pipeline_runner
    runner.submit_batch(
        note=note,
        chat_id=message.chat.id,
        status_message_id=status_msg.message_id,
        loop=loop,
    )


@router.message()
async def handle_message(message: Message, bot: Bot) -> None:
    """Обрабатывает все текстовые сообщения — ищет URL."""
    text = message.text or ""
    match = URL_PATTERN.search(text)

    if not match:
        await message.answer(MSG_NOT_A_LINK)
        return

    url = match.group(0)

    # Парсим ссылку
    try:
        link = parse_url(url)
    except ValueError as e:
        await message.answer(MSG_INVALID_URL.format(details=str(e)))
        return

    db = bot._db
    runner = bot._pipeline_runner

    # Идемпотентность
    existing = db.get_job_by_url(url)
    if existing:
        status = existing["status"]
        if status == "done":
            await message.answer(MSG_ALREADY_DONE)
            return
        elif status in ("downloading", "transcribing", "exporting", "collecting", "analyzing", "saving"):
            await message.answer(MSG_ALREADY_PROCESSING.format(status=status))
            return
        elif status == "error":
            # Авто-retry при повторной отправке
            job_id = existing["id"]
            db.update_job_status(job_id, "pending", retry_count=0)
        elif status == "pending":
            job_id = existing["id"]
        else:
            job_id = existing["id"]
    else:
        # Создаём новую задачу
        is_external = isinstance(link, ExternalLink)
        if is_external:
            job_id = db.create_external_job(link)
        else:
            job_id = db.create_job(link)

    # Отправляем статусное сообщение, которое будет обновляться
    status_msg = await message.answer(MSG_PROCESSING_STARTED)

    # Запускаем пайплайн в фоне
    loop = asyncio.get_running_loop()
    runner.submit(
        job_id=job_id,
        link=link,
        chat_id=message.chat.id,
        status_message_id=status_msg.message_id,
        loop=loop,
    )
