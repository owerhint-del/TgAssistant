"""
Управление event loop для корректной работы asyncio в Python 3.10+.

Использует threading.local для per-thread loop:
- CLI: один поток → один loop на всю команду.
- Web: каждый worker thread получает свой loop, не конфликтуя с FastAPI.
"""
from __future__ import annotations
import asyncio
import threading

_tls = threading.local()


def get_loop() -> asyncio.AbstractEventLoop:
    """Возвращает текущий per-thread loop или создаёт новый."""
    loop = getattr(_tls, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _tls.loop = loop
    return loop


def run_sync(coro):
    """
    Запускает async-корутину синхронно через per-thread event loop.
    Используй вместо asyncio.get_event_loop().run_until_complete().
    """
    return get_loop().run_until_complete(coro)


def safe_disconnect(client) -> None:
    """
    Безопасно отключает Telethon-клиент.

    Telethon 1.42: disconnect() — синхронная функция (сама вызывает run_until_complete).
    Моки/будущие версии: disconnect() может вернуть корутину.
    """
    try:
        result = client.disconnect()
        if asyncio.iscoroutine(result):
            run_sync(result)
    except Exception:
        pass


def close_loop() -> None:
    """Закрывает loop текущего потока."""
    loop = getattr(_tls, "loop", None)
    if loop and not loop.is_closed():
        loop.close()
    _tls.loop = None
