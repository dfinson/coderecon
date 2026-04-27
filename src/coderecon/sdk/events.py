"""SDK event subscription — callbacks and async iterators."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from typing import Any, Callable

import structlog

from coderecon.sdk.types import Event

_log = logging.getLogger(__name__)

class EventRouter:
    """Routes daemon events to callbacks and async queues."""

    def __init__(self) -> None:
        self._callbacks: list[tuple[str, Callable[[Event], Any]]] = []
        self._queues: list[tuple[list[str], asyncio.Queue[Event]]] = []

    def on(self, pattern: str, callback: Callable[[Event], Any]) -> None:
        """Register a callback for events matching *pattern* (glob)."""
        self._callbacks.append((pattern, callback))

    def dispatch(self, event: Event) -> None:
        """Dispatch an event to matching callbacks and queues."""
        for pattern, cb in self._callbacks:
            if fnmatch.fnmatch(event.type, pattern):
                try:
                    cb(event)
                except (RuntimeError, ValueError, TypeError):  # noqa: BLE001
                    _log.debug("Event callback failed", exc_info=True)

        for patterns, queue in self._queues:
            if any(fnmatch.fnmatch(event.type, p) for p in patterns):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    structlog.get_logger().debug("event_queue_full_dropped", event_type=event.type, exc_info=True)
                    pass  # Drop events if consumer is too slow

    def subscribe(self, *patterns: str, maxsize: int = 256) -> asyncio.Queue[Event]:
        """Create a queue that receives events matching *patterns*.

        Use with ``events()`` async iterator.
        """
        if not patterns:
            patterns = ("*",)
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._queues.append((list(patterns), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        """Remove a subscription queue."""
        self._queues = [(p, q) for p, q in self._queues if q is not queue]
