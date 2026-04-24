"""RepoHandle and SessionHandle — bound convenience objects."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from coderecon.sdk.client import CodeRecon
    from coderecon.sdk.types import (
        CheckpointResult,
        CommunitiesResult,
        CyclesResult,
        DiffResult,
        GraphExportResult,
        ImpactResult,
        MapResult,
        ReconResult,
        RefactorCancelResult,
        RefactorCommitResult,
        RefactorResult,
        UnderstandResult,
    )


class SessionHandle:
    """A session-bound proxy — all calls use a fixed session ID.

    Created by ``sdk.session("name")``.
    """

    def __init__(self, sdk: "CodeRecon", session_id: str) -> None:
        self._sdk = sdk
        self._session_id = session_id

    def repo(self, name: str, worktree: str | None = None) -> "RepoHandle":
        """Return a repo-bound handle using this session."""
        return RepoHandle(self._sdk, name, worktree, explicit_session=self._session_id)

    async def close(self) -> None:
        """Send session_close for this explicit session."""
        await self._sdk._call(
            "session_close", {"session_id": self._session_id}, session_id=None,
        )

    # Forward all tool methods with explicit session
    async def recon(self, repo: str, task: str, **kwargs: Any) -> "ReconResult":
        old = self._sdk._explicit_session
        self._sdk._explicit_session = self._session_id
        try:
            return await self._sdk.recon(repo, task, **kwargs)
        finally:
            self._sdk._explicit_session = old

    async def checkpoint(self, repo: str, changed_files: list[str], **kwargs: Any) -> "CheckpointResult":
        old = self._sdk._explicit_session
        self._sdk._explicit_session = self._session_id
        try:
            return await self._sdk.checkpoint(repo, changed_files, **kwargs)
        finally:
            self._sdk._explicit_session = old

    async def refactor_rename(self, repo: str, symbol: str, new_name: str, justification: str, **kwargs: Any) -> "RefactorResult":
        old = self._sdk._explicit_session
        self._sdk._explicit_session = self._session_id
        try:
            return await self._sdk.refactor_rename(repo, symbol, new_name, justification, **kwargs)
        finally:
            self._sdk._explicit_session = old

    async def refactor_move(self, repo: str, from_path: str, to_path: str, justification: str, **kwargs: Any) -> "RefactorResult":
        old = self._sdk._explicit_session
        self._sdk._explicit_session = self._session_id
        try:
            return await self._sdk.refactor_move(repo, from_path, to_path, justification, **kwargs)
        finally:
            self._sdk._explicit_session = old

    async def refactor_commit(self, repo: str, refactor_id: str, **kwargs: Any) -> "RefactorCommitResult":
        old = self._sdk._explicit_session
        self._sdk._explicit_session = self._session_id
        try:
            return await self._sdk.refactor_commit(repo, refactor_id, **kwargs)
        finally:
            self._sdk._explicit_session = old

    async def refactor_cancel(self, repo: str, refactor_id: str, **kwargs: Any) -> "RefactorCancelResult":
        old = self._sdk._explicit_session
        self._sdk._explicit_session = self._session_id
        try:
            return await self._sdk.refactor_cancel(repo, refactor_id, **kwargs)
        finally:
            self._sdk._explicit_session = old

    async def recon_map(self, repo: str, **kwargs: Any) -> "MapResult":
        return await self._sdk.recon_map(repo, **kwargs)

    async def recon_impact(self, repo: str, target: str, justification: str, **kwargs: Any) -> "ImpactResult":
        old = self._sdk._explicit_session
        self._sdk._explicit_session = self._session_id
        try:
            return await self._sdk.recon_impact(repo, target, justification, **kwargs)
        finally:
            self._sdk._explicit_session = old

    async def recon_understand(self, repo: str, **kwargs: Any) -> "UnderstandResult":
        return await self._sdk.recon_understand(repo, **kwargs)

    async def semantic_diff(self, repo: str, **kwargs: Any) -> "DiffResult":
        return await self._sdk.semantic_diff(repo, **kwargs)

    async def graph_cycles(self, repo: str, **kwargs: Any) -> "CyclesResult":
        return await self._sdk.graph_cycles(repo, **kwargs)

    async def graph_communities(self, repo: str, **kwargs: Any) -> "CommunitiesResult":
        return await self._sdk.graph_communities(repo, **kwargs)

    async def graph_export(self, repo: str, **kwargs: Any) -> "GraphExportResult":
        return await self._sdk.graph_export(repo, **kwargs)


class RepoHandle:
    """A repo-bound proxy — all calls have ``repo`` pre-bound.

    Created by ``sdk.repo("name")`` or ``session_handle.repo("name")``.
    """

    def __init__(
        self,
        sdk: "CodeRecon",
        repo: str,
        worktree: str | None = None,
        *,
        explicit_session: str | None = None,
    ) -> None:
        self._sdk = sdk
        self._repo = repo
        self._worktree = worktree
        self._explicit_session = explicit_session

    def _call_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {"repo": self._repo}
        if self._worktree is not None:
            kw["worktree"] = self._worktree
        return kw

    def _with_session(self) -> Any:
        """Context manager to temporarily set explicit session on SDK."""
        class _Ctx:
            def __init__(ctx, sdk: "CodeRecon", session_id: str | None) -> None:
                ctx._sdk = sdk
                ctx._session_id = session_id
                ctx._old: str | None = None
            def __enter__(ctx) -> None:
                ctx._old = ctx._sdk._explicit_session
                if ctx._session_id is not None:
                    ctx._sdk._explicit_session = ctx._session_id
            def __exit__(ctx, *exc: object) -> None:
                ctx._sdk._explicit_session = ctx._old
        return _Ctx(self._sdk, self._explicit_session)

    async def recon(self, task: str, **kwargs: Any) -> "ReconResult":
        with self._with_session():
            return await self._sdk.recon(self._repo, task, worktree=self._worktree, **kwargs)

    async def recon_map(self, **kwargs: Any) -> "MapResult":
        return await self._sdk.recon_map(self._repo, worktree=self._worktree, **kwargs)

    async def recon_impact(self, target: str, justification: str, **kwargs: Any) -> "ImpactResult":
        with self._with_session():
            return await self._sdk.recon_impact(self._repo, target, justification, worktree=self._worktree, **kwargs)

    async def recon_understand(self, **kwargs: Any) -> "UnderstandResult":
        return await self._sdk.recon_understand(self._repo, worktree=self._worktree, **kwargs)

    async def semantic_diff(self, **kwargs: Any) -> "DiffResult":
        return await self._sdk.semantic_diff(self._repo, worktree=self._worktree, **kwargs)

    async def graph_cycles(self, **kwargs: Any) -> "CyclesResult":
        return await self._sdk.graph_cycles(self._repo, worktree=self._worktree, **kwargs)

    async def graph_communities(self, **kwargs: Any) -> "CommunitiesResult":
        return await self._sdk.graph_communities(self._repo, worktree=self._worktree, **kwargs)

    async def graph_export(self, **kwargs: Any) -> "GraphExportResult":
        return await self._sdk.graph_export(self._repo, worktree=self._worktree, **kwargs)

    async def refactor_rename(self, symbol: str, new_name: str, justification: str, **kwargs: Any) -> "RefactorResult":
        with self._with_session():
            return await self._sdk.refactor_rename(self._repo, symbol, new_name, justification, worktree=self._worktree, **kwargs)

    async def refactor_move(self, from_path: str, to_path: str, justification: str, **kwargs: Any) -> "RefactorResult":
        with self._with_session():
            return await self._sdk.refactor_move(self._repo, from_path, to_path, justification, worktree=self._worktree, **kwargs)

    async def refactor_commit(self, refactor_id: str, **kwargs: Any) -> "RefactorCommitResult":
        with self._with_session():
            return await self._sdk.refactor_commit(self._repo, refactor_id, worktree=self._worktree, **kwargs)

    async def refactor_cancel(self, refactor_id: str, **kwargs: Any) -> "RefactorCancelResult":
        with self._with_session():
            return await self._sdk.refactor_cancel(self._repo, refactor_id, worktree=self._worktree, **kwargs)

    async def checkpoint(self, changed_files: list[str], **kwargs: Any) -> "CheckpointResult":
        with self._with_session():
            return await self._sdk.checkpoint(self._repo, changed_files, worktree=self._worktree, **kwargs)

    def as_openai_tools(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool definitions with repo pre-bound."""
        from coderecon.sdk.frameworks import as_openai_tools
        return as_openai_tools(self._sdk, repo=self._repo, worktree=self._worktree, **kwargs)
