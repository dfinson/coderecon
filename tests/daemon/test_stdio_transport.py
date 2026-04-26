"""Tests for coderecon.daemon.stdio_transport."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from coderecon.daemon.stdio_transport import _handle_request, _write_message


class TestWriteMessage:
    """Tests for _write_message."""

    @pytest.mark.asyncio()
    async def test_writes_ndjson_line(self) -> None:
        transport = MagicMock()
        lock = asyncio.Lock()
        data = {"id": 1, "result": "ok"}
        await _write_message(data, transport, lock)
        transport.write.assert_called_once()
        raw = transport.write.call_args[0][0]
        line = raw.decode("utf-8")
        assert line.endswith("\n")
        parsed = json.loads(line.strip())
        assert parsed == {"id": 1, "result": "ok"}

    @pytest.mark.asyncio()
    async def test_uses_compact_json(self) -> None:
        transport = MagicMock()
        lock = asyncio.Lock()
        data = {"key": "value"}
        await _write_message(data, transport, lock)
        raw = transport.write.call_args[0][0].decode("utf-8").strip()
        # Compact JSON: no spaces after : or ,
        assert ": " not in raw
        assert ", " not in raw

    @pytest.mark.asyncio()
    async def test_serializes_under_lock(self) -> None:
        transport = MagicMock()
        lock = asyncio.Lock()
        # Acquire the lock to verify _write_message waits
        async with lock:
            task = asyncio.create_task(_write_message({"id": 1}, transport, lock))
            await asyncio.sleep(0.01)
            # Should not have written yet (lock held)
            transport.write.assert_not_called()
        # After releasing lock, task should complete
        await task
        transport.write.assert_called_once()


class TestHandleRequest:
    """Tests for _handle_request."""

    @pytest.mark.asyncio()
    async def test_dispatches_and_writes_response(self) -> None:
        daemon = MagicMock()
        registry = MagicMock()
        request = {"id": 1, "method": "ping"}
        write_message = AsyncMock()
        bus = MagicMock()

        response = {"id": 1, "result": "pong"}
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "coderecon.daemon.stdio_transport.dispatch",
                AsyncMock(return_value=response),
            )
            await _handle_request(daemon, registry, request, write_message, bus)

        write_message.assert_awaited_once_with(response)

    @pytest.mark.asyncio()
    async def test_writes_error_on_dispatch_exception(self) -> None:
        daemon = MagicMock()
        registry = MagicMock()
        request = {"id": 42, "method": "bad"}
        write_message = AsyncMock()
        bus = MagicMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "coderecon.daemon.stdio_transport.dispatch",
                AsyncMock(side_effect=RuntimeError("boom")),
            )
            await _handle_request(daemon, registry, request, write_message, bus)

        write_message.assert_awaited_once()
        error_msg = write_message.call_args[0][0]
        assert error_msg["id"] == 42
        assert error_msg["error"]["code"] == "INTERNAL"

    @pytest.mark.asyncio()
    async def test_error_response_omits_id_if_missing(self) -> None:
        daemon = MagicMock()
        registry = MagicMock()
        request = {"method": "notify"}  # No id
        write_message = AsyncMock()
        bus = MagicMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "coderecon.daemon.stdio_transport.dispatch",
                AsyncMock(side_effect=RuntimeError("boom")),
            )
            await _handle_request(daemon, registry, request, write_message, bus)

        error_msg = write_message.call_args[0][0]
        assert "id" not in error_msg
        assert error_msg["error"]["code"] == "INTERNAL"
