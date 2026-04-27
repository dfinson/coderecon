"""Stdio transport — NDJSON over async stdin/stdout.

Reads newline-delimited JSON requests from ``stdin``, dispatches them
via :func:`coderecon.daemon.dispatch.dispatch`, and writes responses
(and interleaved events) to ``stdout``.

A :class:`asyncio.Lock` on writes ensures NDJSON lines are never
interleaved, even when events fire concurrently with responses.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from coderecon.daemon.dispatch import dispatch
from coderecon.daemon.event_bus import EventBus, wire_event_hooks

if TYPE_CHECKING:
    from coderecon.catalog.registry import CatalogRegistry
    from coderecon.daemon.global_app import GlobalDaemon

log = structlog.get_logger(__name__)

async def _write_message(
    data: dict[str, Any],
    transport: asyncio.WriteTransport,
    lock: asyncio.Lock,
) -> None:
    """Serialise *data* as a single NDJSON line to stdout."""
    line = json.dumps(data, separators=(",", ":"), default=str) + "\n"
    raw = line.encode("utf-8")
    async with lock:
        transport.write(raw)

async def _handle_request(
    daemon: "GlobalDaemon",
    registry: "CatalogRegistry",
    request: dict[str, Any],
    write_message: Callable[[dict[str, Any]], Awaitable[None]],
    bus: EventBus,
) -> None:
    """Dispatch a single request and write the response."""
    try:
        response = await dispatch(daemon, registry, request, event_bus=bus)
        await write_message(response)
    except Exception:
        log.error("stdio.dispatch_error", exc_info=True)
        request_id = request.get("id")
        await write_message({
            **({"id": request_id} if request_id else {}),
            "error": {"code": "INTERNAL", "message": "Unhandled dispatch error"},
        })

async def run_stdio_loop(
    daemon: "GlobalDaemon",
    registry: "CatalogRegistry",
) -> None:
    """Main stdio transport loop — runs until stdin is closed.

    1. Opens async stdin/stdout streams.
    2. Emits ``daemon.ready`` event.
    3. Reads NDJSON requests line-by-line from stdin.
    4. For each request, dispatches and writes the response.
    """
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_event_loop()
    await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

    transport, _proto = await loop.connect_write_pipe(
        asyncio.BaseProtocol, sys.stdout.buffer,
    )

    write_lock = asyncio.Lock()

    async def write_message(data: dict[str, Any]) -> None:
        await _write_message(data, transport, write_lock)

    # Wire up event bus
    bus = EventBus(write_message, transport=transport)
    wire_event_hooks(daemon, bus)

    # Emit daemon.ready
    repo_names = [r.name for r in registry.list_repos()]
    await bus.emit("daemon.ready", {"repos": repo_names})

    # ── Read loop ──
    while True:
        line = await reader.readline()
        if not line:
            # stdin closed → SDK process is gone
            log.info("stdio.eof")
            break

        line_str = line.decode("utf-8", errors="replace").strip()
        if not line_str:
            continue

        try:
            request = json.loads(line_str)
        except json.JSONDecodeError:
            await write_message({
                "error": {"code": "PARSE_ERROR", "message": "Invalid JSON"},
            })
            continue

        if not isinstance(request, dict):
            await write_message({
                "error": {"code": "PARSE_ERROR", "message": "Expected JSON object"},
            })
            continue

        # Dispatch concurrently — multiple requests can be in flight
        asyncio.create_task(_handle_request(daemon, registry, request, write_message, bus))
