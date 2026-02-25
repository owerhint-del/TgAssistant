"""
Middleware: проверка доступа по whitelist admin_ids.
"""
import logging
from typing import Any, Awaitable, Callable, Set

from aiogram import BaseMiddleware
from aiogram.types import Message

from app.bot.messages import MSG_UNAUTHORIZED

logger = logging.getLogger("tgassistant.bot.middleware")


class AdminOnlyMiddleware(BaseMiddleware):
    """Пропускает только пользователей из whitelist."""

    def __init__(self, admin_ids: list[int]):
        self.admin_ids: Set[int] = set(admin_ids)

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if user is None or user.id not in self.admin_ids:
            uid = user.id if user else "unknown"
            logger.warning("Unauthorized access attempt from user %s", uid)
            await event.answer(MSG_UNAUTHORIZED)
            return None
        return await handler(event, data)
