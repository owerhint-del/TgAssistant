"""
Управление event loop для корректной работы asyncio в Python 3.10+.
Обеспечивает единый loop на протяжении всей команды.
"""
from __future__ import annotations
import asyncio
from typing import Optional

_loop: Optional[asyncio.AbstractEventLoop] = None


def get_loop() -> asyncio.AbstractEventLoop:
    """Возвращает текущий loop или создаёт новый."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def run_sync(coro):
    """
    Запускает async-корутину синхронно через постоянный event loop.
    Используй вместо asyncio.get_event_loop().run_until_complete().
    """
    return get_loop().run_until_complete(coro)


def close_loop() -> None:
    """Закрывает loop после завершения команды."""
    global _loop
    if _loop and not _loop.is_closed():
        _loop.close()
    _loop = None
