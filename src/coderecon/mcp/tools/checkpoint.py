"""Checkpoint MCP tool — lint, test, commit in one call.

Chains:  lint (auto-fix) → affected tests → stage → hooks → commit → push → semantic diff
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import structlog
from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from coderecon.mcp.context import AppContext

from fastmcp import Context

log = structlog.get_logger(__name__)

class ProgressSink(Protocol):
    """Minimal progress reporting interface for checkpoint_pipeline.
    Decouples from FastMCP's Context so the pipeline can be called from
    both MCP tool wrappers and the stdio dispatch layer.
    """
    async def report_progress(self, current: int, total: int, message: str) -> None: ...
    async def info(self, message: str) -> None: ...
    async def warning(self, message: str) -> None: ...

# Test Debt Detection


class _NullProgress:
    """No-op progress sink for callers that don't need progress."""
    async def report_progress(self, current: int, total: int, message: str) -> None:
        """No-op: progress discarded when no listener is attached."""
        return None
    async def info(self, message: str) -> None:
        """No-op: info message discarded when no listener is attached."""
        return None
    async def warning(self, message: str) -> None:
        """No-op: warning discarded when no listener is attached."""
        return None

_DEFAULT_MAX_TEST_HOPS = 0
_COMMIT_MAX_TEST_HOPS = 2


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register checkpoint tool with FastMCP server."""
    from coderecon.mcp.tools.checkpoint_pipeline import checkpoint_pipeline

    @mcp.tool(
        annotations={
            "title": "Checkpoint: lint, test, commit, push",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def checkpoint(
        ctx: Context,
        changed_files: list[str] = Field(
            ...,
            description="Files you changed. Used for impact-aware test selection.",
        ),
        lint: bool = Field(True, description="Run linting"),
        autofix: bool = Field(True, description="Apply lint auto-fixes"),
        tests: bool = Field(True, description="Run affected tests"),
        test_filter: str | None = Field(
            None,
            description="Filter which test names to run within targets "
            "(passed to pytest -k, jest --testNamePattern).",
        ),
        max_test_hops: int | None = Field(
            None,
            description="Max import-graph hop depth for test selection. "
            "0 = direct tests only, 1 = direct + 1 transitive, etc. "
            "Default: 0 (direct only) for fast iteration; auto-escalates "
            "to 2 hops when commit_message is set.",
        ),
        commit_message: str | None = Field(
            None,
            description="If set and checks pass, auto-commit with this message. "
            "Skips commit on failure.",
        ),
        push: bool = Field(
            False,
            description="Push to origin after auto-commit (only used with commit_message).",
        ),
    ) -> dict[str, Any]:
        """Lint, test, and optionally commit+push in one call.
        Chains:
        1. lint (full repo, auto-fix by default) — reports and fixes issues
        2. discover + run tests affected by changed_files (via import graph)
        3. (optional) if commit_message is set and all checks pass:
           stage changed_files → pre-commit hooks → commit → push → lean semantic diff
        Returns combined results with pass/fail verdict.
        """
        session = app_ctx.session_manager.get_or_create(ctx.session_id)
        return await checkpoint_pipeline(
            app_ctx,
            session,
            changed_files=changed_files,
            lint=lint,
            autofix=autofix,
            tests=tests,
            test_filter=test_filter,
            max_test_hops=max_test_hops,
            commit_message=commit_message,
            push=push,
            progress=ctx,
        )

# Re-exports for backward compatibility
from coderecon.mcp.tools.checkpoint_helpers import (  # noqa: E402, F401
    _build_failure_snippets,
    _detect_test_debt,
    _extract_traceback_locations,
    _normalize_selector,
    _run_hook_with_retry,
    _summarize_commit,
    _summarize_run,
    _target_matches_affected_files,
    _validate_commit_message,
    _validate_paths_exist,
    run_hook,
)
from coderecon.mcp.tools.checkpoint_tiered import _summarize_verify  # noqa: E402, F401
