"""
BotProgressCallback: маппинг progress_cb Worker-а → редактирование сообщения в Telegram.

Вызывается из фонового потока (ThreadPoolExecutor), поэтому используем
asyncio.run_coroutine_threadsafe() для отправки в loop aiogram.
"""
import asyncio
import logging
import time
from typing import Optional

from aiogram import Bot

from app.bot.messages import STATUS_MESSAGES

logger = logging.getLogger("tgassistant.bot.progress")

# Минимальный интервал между edit-ами (защита от rate limit Telegram API)
THROTTLE_SEC = 3.0


class BotProgressCallback:
    """
    Прогресс-колбэк, совместимый с Worker(progress_cb=...).

    Сигнатура: progress_cb(job_id, status, **extra)
    """

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        message_id: int,
        loop: asyncio.AbstractEventLoop,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.loop = loop
        self._last_edit_time: float = 0.0
        self._last_status: Optional[str] = None

    def __call__(self, job_id: str, status: str, **extra) -> None:
        """Вызывается Worker-ом из фонового потока."""
        # Дедупликация: пропускаем если статус не изменился
        if status == self._last_status:
            return

        # Throttle: не чаще раза в THROTTLE_SEC
        now = time.monotonic()
        if now - self._last_edit_time < THROTTLE_SEC and status != "done" and status != "error":
            return

        self._last_status = status
        self._last_edit_time = now

        text = STATUS_MESSAGES.get(status, f"⏳ {status}...")

        # Отправляем edit в loop aiogram (thread-safe)
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.bot.edit_message_text(
                    text=text,
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                ),
                self.loop,
            )
            # Ждём результат с таймаутом, чтобы не зависнуть
            future.result(timeout=10)
        except Exception as e:
            # Прогресс — best effort, не ломаем пайплайн
            logger.debug("Progress edit failed (job=%s, status=%s): %s", job_id, status, e)
