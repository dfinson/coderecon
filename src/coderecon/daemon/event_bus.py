"""Event bus — bridges internal daemon signals to NDJSON events on stdout.

Events are daemon-initiated messages (no request ``id``).  They carry an
``event`` field and a ``data`` payload.

Usage::

    bus = EventBus(write_message)
    wire_event_hooks(daemon, bus)   # connects internal hooks → bus.emit()
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Callable, Coroutine

import structlog

if TYPE_CHECKING:
    from coderecon.daemon.global_app import GlobalDaemon

log = structlog.get_logger(__name__)

# Type alias for the async write function provided by the stdio transport.
WriteMessageFn = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]

class EventBus:
    """Collects events and writes them as NDJSON to the stdio transport."""

    def __init__(
        self,
        write_message: WriteMessageFn,
        transport: asyncio.WriteTransport | None = None,
    ) -> None:
        self._write = write_message
        self._transport = transport  # raw asyncio.WriteTransport for sync writes

    async def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Emit a single event to stdout."""
        msg: dict[str, Any] = {
            "event": event_type,
            "ts": time.time(),
        }
        if data:
            msg["data"] = data
        try:
            await self._write(msg)
        except (OSError, RuntimeError):
            log.debug("event_bus.emit_failed", event_name=event_type, exc_info=True)

    def emit_sync(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Emit an event synchronously — for use inside blocking code paths.

        Writes directly to the transport without acquiring the async lock.
        Safe when the caller is the only writer (e.g. inside a blocking
        ``initialize()`` that holds the event loop).
        """
        if self._transport is None:
            return
        import json as _json

        msg: dict[str, Any] = {
            "event": event_type,
            "ts": time.time(),
        }
        if data:
            msg["data"] = data
        raw = (_json.dumps(msg, separators=(",", ":"), default=str) + "\n").encode("utf-8")
        try:
            self._transport.write(raw)
        except (OSError, RuntimeError):
            log.debug("event_bus.emit_sync_failed", event_name=event_type, exc_info=True)

def wire_event_hooks(daemon: "GlobalDaemon", bus: EventBus) -> None:
    """Connect internal daemon lifecycle hooks to the event bus.

    This is called once at stdio startup.  It registers callbacks on the
    daemon so that internal state changes (repo activation, index progress,
    etc.) are forwarded as NDJSON events.

    Hooks are best-effort — failures are logged and swallowed.
    """
    # Future: hook into daemon.on_index_progress, daemon.on_repo_activated,
    # daemon.on_watcher_change, etc. as those internal signals are
    # formalised.  For now this is a placeholder that emits daemon.ready.
    log.debug("event_bus.wired")
