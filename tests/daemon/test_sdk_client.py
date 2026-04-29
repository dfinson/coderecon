"""Tests for coderecon.sdk.client — CodeRecon SDK client."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coderecon.sdk.client import CodeRecon
from coderecon.sdk.protocol import CodeReconError

# ---------------------------------------------------------------------------
# Helpers: mock subprocess that speaks NDJSON
# ---------------------------------------------------------------------------

class MockStdout:
    """Simulates an asyncio StreamReader backed by a list of NDJSON lines."""

    def __init__(self, lines: list[dict[str, Any]] | None = None) -> None:
        self._lines: list[bytes] = []
        if lines:
            for msg in lines:
                self._lines.append(json.dumps(msg).encode() + b"\n")
        self._index = 0

    def add(self, msg: dict[str, Any]) -> None:
        self._lines.append(json.dumps(msg).encode() + b"\n")

    async def readline(self) -> bytes:
        if self._index < len(self._lines):
            line = self._lines[self._index]
            self._index += 1
            return line
        return b""  # EOF

class MockStdin:
    """Captures writes for inspection."""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self._closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True

def _make_mock_process(
    stdout_lines: list[dict[str, Any]] | None = None,
) -> tuple[MagicMock, MockStdin, MockStdout]:
    """Build a mock asyncio.subprocess.Process."""
    stdin = MockStdin()
    stdout = MockStdout(stdout_lines or [])
    proc = MagicMock()
    proc.stdin = stdin
    proc.stdout = stdout
    proc.stderr = MockStdout()
    proc.returncode = None
    proc.wait = AsyncMock(return_value=0)
    proc.terminate = MagicMock()
    return proc, stdin, stdout

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCodeReconLifecycle:
    @pytest.mark.asyncio
    async def test_start_waits_for_ready(self) -> None:
        """SDK.start() blocks until daemon.ready event arrives."""
        proc, stdin, stdout = _make_mock_process([
            {"event": "daemon.ready", "data": {}, "ts": 1.0},
        ])

        sdk = CodeRecon()
        with patch("coderecon.sdk.client.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            await sdk.start()

        assert sdk._process is proc
        await sdk.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_pending(self) -> None:
        """Stopping the SDK cancels all in-flight requests."""
        proc, stdin, stdout = _make_mock_process([
            {"event": "daemon.ready", "data": {}, "ts": 1.0},
        ])

        sdk = CodeRecon()
        with patch("coderecon.sdk.client.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            await sdk.start()

        # Create a pending request that will never resolve
        fut = sdk._pending.create("r_orphan")

        proc.returncode = 0  # simulate process exit
        await sdk.stop()
        assert fut.cancelled()

class TestCodeReconSessionManagement:
    def test_resolve_auto_session(self) -> None:
        sdk = CodeRecon()
        sid1 = sdk._resolve_session_id("repo1", None)
        sid2 = sdk._resolve_session_id("repo1", None)
        assert sid1 == sid2  # Same (repo, worktree) pair → same session

    def test_resolve_different_worktrees(self) -> None:
        sdk = CodeRecon()
        sid1 = sdk._resolve_session_id("repo1", "main")
        sid2 = sdk._resolve_session_id("repo1", "feat")
        assert sid1 != sid2

    def test_explicit_session_overrides(self) -> None:
        sdk = CodeRecon()
        sdk._explicit_session = "ext_agent1"
        sid = sdk._resolve_session_id("repo1", None)
        assert sid == "ext_agent1"

    def test_session_handle(self) -> None:
        sdk = CodeRecon()
        handle = sdk.session("my_agent")
        assert handle._session_id == "ext_my_agent"

    def test_repo_handle(self) -> None:
        sdk = CodeRecon()
        handle = sdk.repo("myrepo", "feat")
        assert handle._repo == "myrepo"
        assert handle._worktree == "feat"

class TestCodeReconCall:
    @pytest.mark.asyncio
    async def test_call_not_started_raises(self) -> None:
        sdk = CodeRecon()
        with pytest.raises(CodeReconError, match="NOT_STARTED"):
            await sdk._call("test", {}, session_id=None)

    @pytest.mark.asyncio
    async def test_call_sends_ndjson(self) -> None:
        """_call writes NDJSON to stdin and resolves when response arrives."""
        proc, stdin, stdout = _make_mock_process([
            {"event": "daemon.ready", "data": {}, "ts": 1.0},
        ])

        sdk = CodeRecon()
        with patch("coderecon.sdk.client.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            await sdk.start()

        # Now manually feed a response for the next request
        # We need to do this after _call sends because _call awaits the future.
        # So we'll resolve the pending request from another task.
        async def respond():
            await asyncio.sleep(0.01)
            # Read what was written and respond to it
            assert len(stdin.written) > 0
            req = json.loads(stdin.written[-1])
            # Deliver the response via the read loop is gone (EOF), so resolve directly
            sdk._pending.resolve({"id": req["id"], "result": {"ok": True}})

        task = asyncio.create_task(respond())
        result = await sdk._call("status", {}, session_id=None)
        await task

        assert result == {"ok": True}

        proc.returncode = 0
        await sdk.stop()
