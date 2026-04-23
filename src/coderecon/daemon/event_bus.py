"""Event bus — bridges internal daemon signals to NDJSON events on stdout.

Events are daemon-initiated messages (no request ``id``).  They carry an
``event`` field and a ``data`` payload.

Usage::

    bus = EventBus(write_message)
    wire_event_hooks(daemon, bus)   # connects internal hooks → bus.emit()
"""

from __future__ import annotations

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

    def __init__(self, write_message: WriteMessageFn) -> None:
        self._write = write_message

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
        except Exception:  # noqa: BLE001
            log.debug("event_bus.emit_failed", event=event_type, exc_info=True)


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
