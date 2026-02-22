"""
Retry-декоратор с экспоненциальным backoff.
"""
import time
import functools
import logging
from typing import Callable, Optional, Tuple, Type

logger = logging.getLogger("tgassistant.retry")


def retry(
    max_attempts: int = 3,
    backoff_sec: float = 30.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable] = None,
):
    """
    Декоратор: повторяет вызов функции при исключении.

    Args:
        max_attempts: максимальное количество попыток
        backoff_sec:  базовая пауза в секундах (удваивается с каждой попыткой)
        exceptions:   какие исключения перехватывать
        on_retry:     callback(attempt, exc) при каждой повторной попытке
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        raise
                    wait = backoff_sec * (2 ** (attempt - 1))
                    logger.warning(
                        "Попытка %d/%d не удалась: %s — повтор через %.0fс",
                        attempt, max_attempts, exc, wait
                    )
                    if on_retry:
                        on_retry(attempt, exc)
                    time.sleep(wait)
            raise last_exc  # не достижимо, но для type-checker
        return wrapper
    return decorator


def retry_async(
    max_attempts: int = 3,
    backoff_sec: float = 30.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable] = None,
):
    """
    Async-версия декоратора retry.
    """
    import asyncio

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        raise
                    wait = backoff_sec * (2 ** (attempt - 1))
                    logger.warning(
                        "Попытка %d/%d не удалась: %s — повтор через %.0fс",
                        attempt, max_attempts, exc, wait
                    )
                    if on_retry:
                        on_retry(attempt, exc)
                    await asyncio.sleep(wait)
            raise last_exc
        return wrapper
    return decorator
