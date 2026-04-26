"""Tests for coderecon.sdk.protocol — NDJSON wire encoding/decoding."""

from __future__ import annotations

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
)


class TestEncoding:
    def test_encode_basic(self) -> None:
        line = encode_request("recon", {"task": "find auth"}, request_id="r1")
        msg = json.loads(line.decode())
        assert msg["id"] == "r1"
        assert msg["method"] == "recon"
        assert msg["params"]["task"] == "find auth"
        assert "session_id" not in msg
        assert line.endswith(b"\n")

    def test_encode_with_session(self) -> None:
        line = encode_request("recon", {"task": "x"}, request_id="r2", session_id="s1")
        msg = json.loads(line.decode())
        assert msg["session_id"] == "s1"

    def test_encode_auto_id(self) -> None:
        line = encode_request("test", {})
        msg = json.loads(line.decode())
        assert msg["id"].startswith("r")
        assert int(msg["id"][1:]) > 0

    def test_decode_basic(self) -> None:
        payload = json.dumps({"id": "r1", "result": {"ok": True}}).encode()
        msg = decode_message(payload)
        assert msg["id"] == "r1"
        assert msg["result"]["ok"] is True


class TestDiscriminators:
    def test_is_event(self) -> None:
        assert is_event({"event": "daemon.ready", "data": {}}) is True
        assert is_event({"id": "r1", "result": {}}) is False
        assert is_event({"event": "x", "id": "r1"}) is False

    def test_is_response(self) -> None:
        assert is_response({"id": "r1", "result": {}}) is True
        assert is_response({"event": "x", "data": {}}) is False


class TestGenerateSessionId:
    def test_format(self) -> None:
        sid = generate_session_id()
        assert sid.startswith("sess_")
        assert len(sid) == len("sess_") + 12  # 6 bytes = 12 hex chars

    def test_unique(self) -> None:
        ids = {generate_session_id() for _ in range(100)}
        assert len(ids) == 100


class TestPendingRequests:
    @pytest.mark.asyncio
    async def test_resolve_success(self) -> None:
        pending = PendingRequests()
        fut = pending.create("r1")
        assert pending.resolve({"id": "r1", "result": {"ok": True}}) is True
        assert (await fut) == {"ok": True}

    @pytest.mark.asyncio
    async def test_resolve_error(self) -> None:
        pending = PendingRequests()
        fut = pending.create("r2")
        pending.resolve({"id": "r2", "error": {"code": "FAIL", "message": "broken"}})
        with pytest.raises(CodeReconError, match="FAIL"):
            await fut

    @pytest.mark.asyncio
    async def test_resolve_unknown_id(self) -> None:
        pending = PendingRequests()
        assert pending.resolve({"id": "unknown", "result": {}}) is False

    @pytest.mark.asyncio
    async def test_cancel_all(self) -> None:
        pending = PendingRequests()
        fut1 = pending.create("r1")
        fut2 = pending.create("r2")
        pending.cancel_all()
        assert fut1.cancelled()
        assert fut2.cancelled()


class TestCodeReconError:
    def test_from_wire(self) -> None:
        err = CodeReconError.from_wire({"code": "NOT_FOUND", "message": "repo gone"})
        assert err.code == "NOT_FOUND"
        assert err.message == "repo gone"
        assert "[NOT_FOUND]" in str(err)
