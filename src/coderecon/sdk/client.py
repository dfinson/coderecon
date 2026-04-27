"""CodeRecon SDK client — spawns daemon, exposes tools as async callables.

Usage::

    from coderecon import CodeRecon

    async with CodeRecon() as sdk:
        await sdk.register("/path/to/repo")
        result = await sdk.recon(repo="my-project", task="find auth")
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from collections.abc import AsyncIterator

import structlog

from coderecon.sdk.events import EventRouter
from coderecon.sdk.protocol import (
    CodeReconError,
    PendingRequests,
    decode_message,
    encode_request,
    generate_session_id,
    is_event,
    next_request_id,
)
from coderecon.sdk.types import (
    CatalogEntry,
    CheckpointResult,
    CommunitiesResult,
    CyclesResult,
    DescribeResult,
    DiffResult,
    Event,
    GraphExportResult,
    ImpactResult,
    MapResult,
    ReconResult,
    RefactorCancelResult,
    RefactorCommitResult,
    RefactorResult,
    RegisterResult,
    StatusResult,
    UnderstandResult,
    _to_checkpoint_result,
    _to_communities_result,
    _to_cycles_result,
    _to_describe_result,
    _to_diff_result,
    _to_graph_export_result,
    _to_impact_result,
    _to_map_result,
    _to_recon_result,
    _to_refactor_cancel_result,
    _to_refactor_commit_result,
    _to_refactor_result,
    _to_register_result,
    _to_status_result,
    _to_understand_result,
)

if TYPE_CHECKING:
    from coderecon.sdk.handle import RepoHandle, SessionHandle

log = structlog.get_logger(__name__)

_SENTINEL = object()

# Default timeout for waiting for daemon.ready
_READY_TIMEOUT_SEC = 30.0
# Default timeout for awaiting a tool response
_CALL_TIMEOUT_SEC = 300.0

class CodeRecon:
    """CodeRecon SDK — spawns the global daemon and exposes tools as callables."""

    def __init__(
        self,
        *,
        binary: str | None = None,
        home: str | Path | None = None,
    ) -> None:
        self._binary = binary
        self._home = str(home) if home else None
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending = PendingRequests()
        self._event_router = EventRouter()
        self._sessions: dict[tuple[str, str | None], str] = {}
        self._explicit_session: str | None = None
        self._ready_event = asyncio.Event()

    # ── Lifecycle ──

    async def start(self) -> None:
        """Spawn the daemon child process. Blocks until ``daemon.ready``."""
        binary = self._binary or shutil.which("recon") or "recon"
        cmd = [binary, "up", "--stdio"]
        if self._home:
            cmd.extend(["--home", self._home])

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._wait_ready(_READY_TIMEOUT_SEC)

    async def stop(self) -> None:
        """Clean shutdown — close sessions, then kill daemon."""
        # Best-effort session cleanup
        for session_id in list(self._sessions.values()):
            try:
                await self._call("session_close", {"session_id": session_id}, session_id=None)
            except (OSError, RuntimeError, ValueError):  # noqa: BLE001
                log.debug("client.session_close.failed", session_id=session_id, exc_info=True)

        if self._process and self._process.returncode is None:
            if self._process.stdin:
                self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.terminate()
                await self._process.wait()

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                structlog.get_logger().debug("reader_task_cancelled", exc_info=True)
                pass

        self._pending.cancel_all()

    async def __aenter__(self) -> CodeRecon:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # ── Session Management ──

    def session(self, name: str) -> SessionHandle:
        """Create an explicit named session for multi-agent scenarios."""
        from coderecon.sdk.handle import SessionHandle

        return SessionHandle(self, f"ext_{name}")

    async def close_session(self, repo: str, worktree: str | None = None) -> None:
        """Close the auto-generated session for a (repo, worktree) pair."""
        key = (repo, worktree)
        session_id = self._sessions.pop(key, None)
        if session_id:
            await self._call("session_close", {"session_id": session_id, "repo": repo, "worktree": worktree}, session_id=None)

    def repo(self, name: str, worktree: str | None = None) -> RepoHandle:
        """Return a repo-bound handle with pre-bound tool methods."""
        from coderecon.sdk.handle import RepoHandle

        return RepoHandle(self, name, worktree)

    # ── Events ──

    def on(self, pattern: str, callback: Callable[[Event], Any]) -> None:
        """Register a callback for events matching *pattern* (glob)."""
        self._event_router.on(pattern, callback)

    async def events(self, *patterns: str) -> AsyncIterator[Event]:
        """Async iterator over daemon events."""
        queue = self._event_router.subscribe(*patterns)
        try:
            while True:
                yield await queue.get()
        finally:
            self._event_router.unsubscribe(queue)

    # ── Tool Methods ──

    async def recon(
        self,
        repo: str,
        task: str,
        *,
        seeds: list[str] | None = None,
        pins: list[str] | None = None,
        worktree: str | None = None,
    ) -> ReconResult:
        params: dict[str, Any] = {"repo": repo, "task": task, "worktree": worktree}
        if seeds:
            params["seeds"] = seeds
        if pins:
            params["pins"] = pins
        return _to_recon_result(await self._tool_call("recon", params))

    async def recon_map(self, repo: str, *, worktree: str | None = None) -> MapResult:
        return _to_map_result(await self._tool_call("recon_map", {"repo": repo, "worktree": worktree}))

    async def recon_impact(
        self,
        repo: str,
        target: str,
        justification: str,
        *,
        include_comments: bool = True,
        worktree: str | None = None,
    ) -> ImpactResult:
        return _to_impact_result(await self._tool_call("recon_impact", {
            "repo": repo, "target": target, "justification": justification,
            "include_comments": include_comments, "worktree": worktree,
        }))

    async def recon_understand(self, repo: str, *, worktree: str | None = None) -> UnderstandResult:
        return _to_understand_result(await self._tool_call("recon_understand", {"repo": repo, "worktree": worktree}))

    async def semantic_diff(
        self,
        repo: str,
        *,
        base: str = "HEAD",
        target: str | None = None,
        paths: list[str] | None = None,
        scope_id: str | None = None,
        worktree: str | None = None,
    ) -> DiffResult:
        params: dict[str, Any] = {"repo": repo, "base": base, "worktree": worktree}
        if target is not None:
            params["target"] = target
        if paths is not None:
            params["paths"] = paths
        if scope_id is not None:
            params["scope_id"] = scope_id
        return _to_diff_result(await self._tool_call("semantic_diff", params))

    async def graph_cycles(
        self, repo: str, *, level: str = "file", worktree: str | None = None,
    ) -> CyclesResult:
        return _to_cycles_result(await self._tool_call("graph_cycles", {"repo": repo, "level": level, "worktree": worktree}))

    async def graph_communities(
        self, repo: str, *, level: str = "file", resolution: float = 1.0, worktree: str | None = None,
    ) -> CommunitiesResult:
        return _to_communities_result(await self._tool_call("graph_communities", {
            "repo": repo, "level": level, "resolution": resolution, "worktree": worktree,
        }))

    async def graph_export(
        self, repo: str, *, output_path: str = "", resolution: float = 1.0, worktree: str | None = None,
    ) -> GraphExportResult:
        return _to_graph_export_result(await self._tool_call("graph_export", {
            "repo": repo, "output_path": output_path, "resolution": resolution, "worktree": worktree,
        }))

    async def refactor_rename(
        self,
        repo: str,
        symbol: str,
        new_name: str,
        justification: str,
        *,
        include_comments: bool = True,
        contexts: list[str] | None = None,
        worktree: str | None = None,
    ) -> RefactorResult:
        params: dict[str, Any] = {
            "repo": repo, "symbol": symbol, "new_name": new_name,
            "justification": justification, "include_comments": include_comments,
            "worktree": worktree,
        }
        if contexts is not None:
            params["contexts"] = contexts
        return _to_refactor_result(await self._tool_call("refactor_rename", params))

    async def refactor_move(
        self,
        repo: str,
        from_path: str,
        to_path: str,
        justification: str,
        *,
        include_comments: bool = True,
        worktree: str | None = None,
    ) -> RefactorResult:
        return _to_refactor_result(await self._tool_call("refactor_move", {
            "repo": repo, "from_path": from_path, "to_path": to_path,
            "justification": justification, "include_comments": include_comments,
            "worktree": worktree,
        }))

    async def refactor_commit(
        self,
        repo: str,
        refactor_id: str,
        *,
        inspect_path: str | None = None,
        context_lines: int = 2,
        worktree: str | None = None,
    ) -> RefactorCommitResult:
        return _to_refactor_commit_result(await self._tool_call("refactor_commit", {
            "repo": repo, "refactor_id": refactor_id,
            "inspect_path": inspect_path, "context_lines": context_lines,
            "worktree": worktree,
        }))

    async def refactor_cancel(
        self, repo: str, refactor_id: str, *, worktree: str | None = None,
    ) -> RefactorCancelResult:
        return _to_refactor_cancel_result(await self._tool_call("refactor_cancel", {
            "repo": repo, "refactor_id": refactor_id, "worktree": worktree,
        }))

    async def checkpoint(
        self,
        repo: str,
        changed_files: list[str],
        *,
        lint: bool = True,
        autofix: bool = True,
        tests: bool = True,
        test_filter: str | None = None,
        max_test_hops: int | None = None,
        commit_message: str | None = None,
        push: bool = False,
        worktree: str | None = None,
    ) -> CheckpointResult:
        params: dict[str, Any] = {
            "repo": repo, "changed_files": changed_files,
            "lint": lint, "autofix": autofix, "tests": tests,
            "push": push, "worktree": worktree,
        }
        if test_filter is not None:
            params["test_filter"] = test_filter
        if max_test_hops is not None:
            params["max_test_hops"] = max_test_hops
        if commit_message is not None:
            params["commit_message"] = commit_message
        return _to_checkpoint_result(await self._tool_call("checkpoint", params))

    async def describe(
        self,
        action: str,
        *,
        name: str | None = None,
        code: str | None = None,
    ) -> DescribeResult:
        params: dict[str, Any] = {"action": action}
        if name is not None:
            params["name"] = name
        if code is not None:
            params["code"] = code
        # describe is a management-ish method — no repo/session
        return _to_describe_result(await self._call("describe", params, session_id=None))

    # ── Management Methods ──

    async def register(self, path: str | Path) -> RegisterResult:
        return _to_register_result(await self._call("register", {"path": str(path)}, session_id=None))

    async def unregister(self, path: str | Path) -> bool:
        result = await self._call("unregister", {"path": str(path)}, session_id=None)
        return result.get("removed", False)

    async def catalog(self) -> list[CatalogEntry]:
        result = await self._call("catalog", {}, session_id=None)
        return [
            CatalogEntry(
                name=r.get("name", ""),
                git_dir=r.get("git_dir", ""),
                worktrees=r.get("worktrees", []),
                raw=r,
            )
            for r in result.get("repos", [])
        ]

    async def status(self, repo: str | None = None) -> StatusResult:
        params: dict[str, Any] = {}
        if repo:
            params["repo"] = repo
        return _to_status_result(await self._call("status", params, session_id=None))

    async def reindex(self, repo: str, worktree: str | None = None) -> None:
        await self._call("reindex", {"repo": repo, "worktree": worktree}, session_id=None)

    # ── Framework Adapters ──

    def as_openai_tools(self, repo: str, worktree: str | None = None) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool definitions with repo pre-bound."""
        from coderecon.sdk.frameworks import as_openai_tools
        return as_openai_tools(self, repo=repo, worktree=worktree)

    def as_langchain_tools(self, repo: str, worktree: str | None = None) -> list[Any]:
        """Return LangChain StructuredTool instances with repo pre-bound."""
        from coderecon.sdk.frameworks import as_langchain_tools
        return as_langchain_tools(self, repo=repo, worktree=worktree)

    # ── Internal RPC ──

    def _resolve_session_id(self, repo: str, worktree: str | None) -> str:
        """Get or create the session ID for this (repo, worktree) pair."""
        if self._explicit_session is not None:
            return self._explicit_session
        key = (repo, worktree)
        if key not in self._sessions:
            self._sessions[key] = generate_session_id()
        return self._sessions[key]

    async def _tool_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Call a tool method (auto-resolves session from repo/worktree)."""
        repo = params.get("repo")
        worktree = params.get("worktree")
        session_id = self._resolve_session_id(repo, worktree) if repo else None
        return await self._call(method, params, session_id=session_id)

    async def _call(
        self,
        method: str,
        params: dict[str, Any],
        session_id: str | None = _SENTINEL,  # type: ignore[assignment]
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a request and await the response."""
        if self._process is None or self._process.returncode is not None:
            raise CodeReconError("NOT_STARTED", "Daemon not running. Call start() first.")

        request_id = next_request_id()
        sid = session_id if session_id is not _SENTINEL else None  # type: ignore[comparison-overlap]

        line = encode_request(method, params, request_id=request_id, session_id=sid)
        fut = self._pending.create(request_id)

        if self._process.stdin is None:
            raise RuntimeError("Process stdin not available — was start() called?")
        self._process.stdin.write(line)
        await self._process.stdin.drain()

        return await asyncio.wait_for(fut, timeout=timeout)

    async def _wait_ready(self, timeout: float) -> None:
        """Wait for the daemon.ready event."""
        self._event_router.on("daemon.ready", lambda _: self._ready_event.set())
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
        except TimeoutError:
            raise CodeReconError("TIMEOUT", "Daemon did not send daemon.ready within timeout") from None

    async def _read_loop(self) -> None:
        """Background task: read stdout, route responses and events."""
        assert self._process is not None
        assert self._process.stdout is not None

        while True:
            line = await self._process.stdout.readline()
            if not line:
                log.debug("sdk.read_loop.eof")
                self._pending.cancel_all()
                break

            try:
                msg = decode_message(line)
            except (ValueError, KeyError, UnicodeDecodeError):  # noqa: BLE001
                log.debug("sdk.read_loop.decode_error", line=line[:200])
                continue

            if is_event(msg):
                event = Event(
                    type=msg["event"],
                    data=msg.get("data", {}),
                    ts=msg.get("ts", 0.0),
                )
                self._event_router.dispatch(event)
            else:
                self._pending.resolve(msg)
