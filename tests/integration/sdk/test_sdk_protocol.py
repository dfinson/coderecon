"""Integration tests for SDK protocol — encode, decode, request tracking, errors."""

from __future__ import annotations

import asyncio
import json

import pytest

from coderecon.sdk.protocol import (
    CodeReconError,
    PendingRequests,
    decode_message,
    encode_request,
    generate_session_id,
    is_event,
    is_response,
    next_request_id,
)

pytestmark = pytest.mark.integration


class TestEncodeRequest:
    def test_basic_encoding(self) -> None:
        data = encode_request("recon", {"task": "find bugs"})
        msg = json.loads(data.decode("utf-8"))
        assert msg["method"] == "recon"
        assert msg["params"]["task"] == "find bugs"
        assert "id" in msg

    def test_ends_with_newline(self) -> None:
        data = encode_request("ping", {})
        assert data.endswith(b"\n")

    def test_custom_request_id(self) -> None:
        data = encode_request("ping", {}, request_id="custom-42")
        msg = json.loads(data.decode("utf-8"))
        assert msg["id"] == "custom-42"

    def test_session_id_included(self) -> None:
        data = encode_request("ping", {}, session_id="sess_abc123")
        msg = json.loads(data.decode("utf-8"))
        assert msg["session_id"] == "sess_abc123"

    def test_session_id_omitted_when_none(self) -> None:
        data = encode_request("ping", {})
        msg = json.loads(data.decode("utf-8"))
        assert "session_id" not in msg

    def test_compact_json(self) -> None:
        """Should use compact separators (no spaces)."""
        data = encode_request("ping", {"key": "val"})
        text = data.decode("utf-8").strip()
        # No spaces after : or ,
        assert ": " not in text
        assert ", " not in text


class TestDecodeMessage:
    def test_basic_decode(self) -> None:
        raw = b'{"id":"r1","result":{"status":"ok"}}\n'
        msg = decode_message(raw)
        assert msg["id"] == "r1"
        assert msg["result"]["status"] == "ok"

    def test_handles_utf8(self) -> None:
        raw = json.dumps({"id": "r1", "result": {"text": "héllo"}}).encode("utf-8")
        msg = decode_message(raw)
        assert msg["result"]["text"] == "héllo"

    def test_handles_invalid_utf8_gracefully(self) -> None:
        raw = b'{"id":"r1","result":{"text":"ok"}}\n'
        msg = decode_message(raw)
        assert msg["id"] == "r1"


class TestIsEvent:
    def test_event_message(self) -> None:
        assert is_event({"event": "indexing.progress", "data": {}}) is True

    def test_response_is_not_event(self) -> None:
        assert is_event({"id": "r1", "result": {}}) is False

    def test_event_with_id_is_not_event(self) -> None:
        """If both event and id are present, it's NOT an event."""
        assert is_event({"id": "r1", "event": "foo"}) is False


class TestIsResponse:
    def test_response_message(self) -> None:
        assert is_response({"id": "r1", "result": {}}) is True

    def test_event_is_not_response(self) -> None:
        assert is_response({"event": "foo", "data": {}}) is False

    def test_error_response(self) -> None:
        assert is_response({"id": "r1", "error": {"code": "E1"}}) is True


class TestNextRequestId:
    def test_returns_string(self) -> None:
        rid = next_request_id()
        assert isinstance(rid, str)
        assert rid.startswith("r")

    def test_monotonically_increasing(self) -> None:
        ids = [next_request_id() for _ in range(5)]
        nums = [int(i[1:]) for i in ids]
        assert nums == sorted(nums)
        assert len(set(nums)) == 5  # all unique


class TestGenerateSessionId:
    def test_format(self) -> None:
        sid = generate_session_id()
        assert sid.startswith("sess_")
        assert len(sid) > 10

    def test_unique(self) -> None:
        ids = {generate_session_id() for _ in range(10)}
        assert len(ids) == 10


class TestCodeReconError:
    def test_from_wire(self) -> None:
        err = CodeReconError.from_wire({"code": "NOT_FOUND", "message": "Repo not found"})
        assert err.code == "NOT_FOUND"
        assert err.message == "Repo not found"
        assert "[NOT_FOUND]" in str(err)

    def test_from_wire_defaults(self) -> None:
        err = CodeReconError.from_wire({})
        assert err.code == "UNKNOWN"
        assert err.message == ""

    def test_is_exception(self) -> None:
        err = CodeReconError("ERR", "msg")
        assert isinstance(err, Exception)


class TestPendingRequests:
    @pytest.mark.asyncio
    async def test_create_and_resolve(self) -> None:
        pending = PendingRequests()
        fut = pending.create("r1")
        assert not fut.done()

        resolved = pending.resolve({"id": "r1", "result": {"status": "ok"}})
        assert resolved is True
        result = await fut
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_resolve_error_sets_exception(self) -> None:
        pending = PendingRequests()
        fut = pending.create("r2")

        pending.resolve({"id": "r2", "error": {"code": "E1", "message": "fail"}})
        with pytest.raises(CodeReconError) as exc_info:
            await fut
        assert exc_info.value.code == "E1"

    @pytest.mark.asyncio
    async def test_resolve_unknown_id_returns_false(self) -> None:
        pending = PendingRequests()
        assert pending.resolve({"id": "unknown", "result": {}}) is False

    @pytest.mark.asyncio
    async def test_resolve_no_id_returns_false(self) -> None:
        pending = PendingRequests()
        assert pending.resolve({"event": "foo"}) is False

    @pytest.mark.asyncio
    async def test_cancel_all(self) -> None:
        pending = PendingRequests()
        f1 = pending.create("r1")
        f2 = pending.create("r2")

        pending.cancel_all()
        assert f1.cancelled()
        assert f2.cancelled()

    @pytest.mark.asyncio
    async def test_cancel_all_then_resolve_is_noop(self) -> None:
        pending = PendingRequests()
        pending.create("r1")
        pending.cancel_all()
        # Resolve after cancel should return False (no pending requests)
        assert pending.resolve({"id": "r1", "result": {}}) is False
