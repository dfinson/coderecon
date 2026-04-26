"""Tests for coderecon.daemon.event_bus."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from coderecon.daemon.event_bus import EventBus, wire_event_hooks


class TestEventBus:
    """Tests for EventBus.emit and emit_sync."""

    @pytest.fixture()
    def write_message(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture()
    def bus(self, write_message: AsyncMock) -> EventBus:
        return EventBus(write_message)

    @pytest.mark.asyncio()
    async def test_emit_sends_event_type(self, bus: EventBus, write_message: AsyncMock) -> None:
        await bus.emit("test.event")
        write_message.assert_awaited_once()
        msg = write_message.call_args[0][0]
        assert msg["event"] == "test.event"

    @pytest.mark.asyncio()
    async def test_emit_includes_timestamp(self, bus: EventBus, write_message: AsyncMock) -> None:
        await bus.emit("ping")
        msg = write_message.call_args[0][0]
        assert "ts" in msg
        assert isinstance(msg["ts"], float)

    @pytest.mark.asyncio()
    async def test_emit_with_data(self, bus: EventBus, write_message: AsyncMock) -> None:
        await bus.emit("status", {"repos": ["a", "b"]})
        msg = write_message.call_args[0][0]
        assert msg["data"] == {"repos": ["a", "b"]}

    @pytest.mark.asyncio()
    async def test_emit_without_data_omits_key(
        self, bus: EventBus, write_message: AsyncMock
    ) -> None:
        await bus.emit("heartbeat")
        msg = write_message.call_args[0][0]
        assert "data" not in msg

    @pytest.mark.asyncio()
    async def test_emit_swallows_os_error(self, write_message: AsyncMock) -> None:
        write_message.side_effect = OSError("broken pipe")
        bus = EventBus(write_message)
        # Should not raise
        await bus.emit("fail.event")

    @pytest.mark.asyncio()
    async def test_emit_swallows_runtime_error(self, write_message: AsyncMock) -> None:
        write_message.side_effect = RuntimeError("event loop closed")
        bus = EventBus(write_message)
        await bus.emit("fail.event")


class TestEventBusEmitSync:
    """Tests for EventBus.emit_sync."""

    def test_emit_sync_writes_ndjson_to_transport(self) -> None:
        transport = MagicMock()
        bus = EventBus(AsyncMock(), transport=transport)
        bus.emit_sync("sync.event", {"key": "val"})
        transport.write.assert_called_once()
        raw = transport.write.call_args[0][0]
        line = raw.decode("utf-8").strip()
        parsed = json.loads(line)
        assert parsed["event"] == "sync.event"
        assert parsed["data"] == {"key": "val"}

    def test_emit_sync_includes_timestamp(self) -> None:
        transport = MagicMock()
        bus = EventBus(AsyncMock(), transport=transport)
        bus.emit_sync("tick")
        raw = transport.write.call_args[0][0]
        parsed = json.loads(raw.decode("utf-8").strip())
        assert isinstance(parsed["ts"], float)

    def test_emit_sync_without_data_omits_key(self) -> None:
        transport = MagicMock()
        bus = EventBus(AsyncMock(), transport=transport)
        bus.emit_sync("ping")
        raw = transport.write.call_args[0][0]
        parsed = json.loads(raw.decode("utf-8").strip())
        assert "data" not in parsed

    def test_emit_sync_no_transport_is_noop(self) -> None:
        bus = EventBus(AsyncMock(), transport=None)
        # Should not raise
        bus.emit_sync("noop.event")

    def test_emit_sync_swallows_os_error(self) -> None:
        transport = MagicMock()
        transport.write.side_effect = OSError("broken")
        bus = EventBus(AsyncMock(), transport=transport)
        bus.emit_sync("fail")

    def test_emit_sync_swallows_runtime_error(self) -> None:
        transport = MagicMock()
        transport.write.side_effect = RuntimeError("closed")
        bus = EventBus(AsyncMock(), transport=transport)
        bus.emit_sync("fail")


class TestWireEventHooks:
    """Tests for wire_event_hooks."""

    def test_wire_event_hooks_does_not_raise(self) -> None:
        daemon = MagicMock()
        bus = EventBus(AsyncMock())
        wire_event_hooks(daemon, bus)
