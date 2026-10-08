"""In-process pub/sub for the console's SSE stream."""
from __future__ import annotations

import asyncio
import json
from typing import Any

_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(kind: str, payload: Any) -> None:
    """Thread-safe: callable from worker threads."""
    msg = json.dumps({"type": kind, "data": payload}, default=str)
    if _loop is None:
        return

    def _put() -> None:
        for q in list(_subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    _loop.call_soon_threadsafe(_put)
