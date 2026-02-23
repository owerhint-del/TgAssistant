"""
SSE (Server-Sent Events) route for real-time job updates.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.web.services.event_bus import event_bus

logger = logging.getLogger("tgassistant.web.sse")

router = APIRouter()


@router.get("/api/events")
async def event_stream(request: Request):
    """
    SSE endpoint. Browser connects via EventSource('/api/events').
    Receives job_update events as JSON strings.
    """

    async def generate():
        queue = event_bus.subscribe()
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {
                        "event": event.get("type", "message"),
                        "data": json.dumps(event),
                    }
                except asyncio.TimeoutError:
                    # Send keepalive comment to prevent connection timeout
                    yield {"comment": "keepalive"}
        finally:
            event_bus.unsubscribe(queue)

    return EventSourceResponse(generate())
