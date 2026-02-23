"""
In-memory pub/sub для SSE-обновлений статуса задач.
Подписчики получают события в реальном времени через asyncio.Queue.

Thread-safety: publish() вызывается из фоновых потоков pipeline,
subscribe/unsubscribe — из asyncio event loop (SSE route).
Используем threading.Lock для защиты _subscribers и
loop.call_soon_threadsafe для безопасной доставки в asyncio.Queue.
"""
import asyncio
import logging
import threading
from typing import Optional, Set

logger = logging.getLogger("tgassistant.web.events")


class EventBus:
    """Thread-safe in-memory pub/sub for SSE delivery."""

    def __init__(self):
        self._subscribers: Set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue. Must be called from the async event loop."""
        # Capture the event loop on first subscribe (SSE route runs in FastAPI's loop)
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subscribers.add(q)
        logger.debug("SSE subscriber added (total: %d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)
        logger.debug("SSE subscriber removed (total: %d)", len(self._subscribers))

    def publish(self, event: dict) -> None:
        """
        Publish an event to all subscribers. Safe to call from any thread.
        Uses call_soon_threadsafe when an event loop is available.
        Falls back to direct _safe_put only when no loop exists (sync tests)
        or loop is closed (shutdown).
        """
        with self._lock:
            subscribers = list(self._subscribers)

        if not subscribers:
            return

        loop = self._loop
        for q in subscribers:
            try:
                if loop is not None:
                    # Always go through the event loop when one exists
                    loop.call_soon_threadsafe(self._safe_put, q, event)
                else:
                    # No async loop captured — pure sync context (tests only)
                    self._safe_put(q, event)
            except RuntimeError:
                # Loop is closed — direct put is safe (no concurrent consumers)
                try:
                    self._safe_put(q, event)
                except Exception:
                    pass
            except Exception:
                pass  # best-effort delivery

    def _safe_put(self, q: asyncio.Queue, event: dict) -> None:
        """Callback executed on the event loop thread."""
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Subscriber is too slow — drop oldest event and retry
            try:
                q.get_nowait()
                q.put_nowait(event)
            except Exception:
                pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Global singleton — created once, shared across the app
event_bus = EventBus()
