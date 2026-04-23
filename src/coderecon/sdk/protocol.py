"""Stdio JSON wire format — encode, decode, correlate."""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

_COUNTER = 0


def next_request_id() -> str:
    """Generate a monotonically increasing request ID."""
    global _COUNTER  # noqa: PLW0603
    _COUNTER += 1
    return f"r{_COUNTER}"


def encode_request(
    method: str,
    params: dict[str, Any],
    *,
    request_id: str | None = None,
    session_id: str | None = None,
) -> bytes:
    """Encode a request as an NDJSON line (bytes)."""
    if request_id is None:
        request_id = next_request_id()
    msg: dict[str, Any] = {"id": request_id, "method": method, "params": params}
    if session_id is not None:
        msg["session_id"] = session_id
    return json.dumps(msg, separators=(",", ":"), default=str).encode("utf-8") + b"\n"


def decode_message(line: bytes) -> dict[str, Any]:
    """Decode a single NDJSON line from stdout."""
    return json.loads(line.decode("utf-8", errors="replace"))


def is_event(msg: dict[str, Any]) -> bool:
    """True if the message is a daemon-initiated event (no ``id``)."""
    return "event" in msg and "id" not in msg


def is_response(msg: dict[str, Any]) -> bool:
    """True if the message is a response to a request (has ``id``)."""
    return "id" in msg


def generate_session_id() -> str:
    """Generate a session ID for auto-session management."""
    return f"sess_{secrets.token_hex(6)}"


class PendingRequests:
    """Track in-flight requests and match responses by ``id``."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def create(self, request_id: str) -> asyncio.Future[dict[str, Any]]:
        """Register a pending request. Returns a Future that resolves on response."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = fut
        return fut

    def resolve(self, msg: dict[str, Any]) -> bool:
        """Resolve a pending request with a response. Returns True if matched."""
        request_id = msg.get("id")
        if request_id is None:
            return False
        fut = self._pending.pop(request_id, None)
        if fut is None:
            return False
        if "error" in msg:
            fut.set_exception(CodeReconError.from_wire(msg["error"]))
        else:
            fut.set_result(msg.get("result", {}))
        return True

    def cancel_all(self) -> None:
        """Cancel all pending requests (daemon shutting down)."""
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()


class CodeReconError(Exception):
    """Error returned by the daemon."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")

    @classmethod
    def from_wire(cls, error: dict[str, Any]) -> CodeReconError:
        return cls(error.get("code", "UNKNOWN"), error.get("message", ""))
