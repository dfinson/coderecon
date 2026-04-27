"""FastMCP server creation and wiring.

Uses native FastMCP @mcp.tool decorators for tool registration.
Includes logging middleware for tool call instrumentation.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from coderecon.mcp.context import AppContext

log = structlog.get_logger(__name__)

_rich_handler_patched = False

def _patch_rich_handler() -> None:
    """Suppress Rich tracebacks by patching RichHandler.emit.

    FastMCP configures RichHandler at import time with rich_tracebacks=True.
    This must be called before importing fastmcp.
    """
    global _rich_handler_patched  # noqa: PLW0603
    if _rich_handler_patched:
        return

    from rich.logging import RichHandler as _RichHandler

    _original_rich_emit = _RichHandler.emit

    def _patched_rich_emit(self: _RichHandler, record: logging.LogRecord) -> None:
        if getattr(self, "rich_tracebacks", False) and record.exc_info:
            record.exc_info = None
            record.exc_text = None
        _original_rich_emit(self, record)

    _RichHandler.emit = _patched_rich_emit  # type: ignore[method-assign]
    _rich_handler_patched = True

@asynccontextmanager
async def _noop_docket_lifespan(*_args: Any, **_kwargs: Any) -> AsyncIterator[None]:
    """No-op lifespan that replaces FastMCP's Docket task queue.

    FastMCP's Docket uses an in-memory backend that polls continuously,
    burning ~15% CPU at idle. Since we don't use Docket's task scheduling,
    we replace the lifespan with a no-op to eliminate this CPU drain.
    """
    yield

def _patch_fastmcp_docket() -> None:
    """Disable FastMCP's Docket task queue to eliminate idle CPU usage.

    The Docket in-memory backend polls at ~5Hz even with no tasks,
    causing unnecessary CPU usage. Since CodeRecon doesn't use Docket,
    we monkey-patch the lifespan to be a no-op.
    """
    from fastmcp import FastMCP

    # Only patch once
    if not hasattr(FastMCP, "_docket_patched") and hasattr(FastMCP, "_docket_lifespan"):
        FastMCP._docket_lifespan = staticmethod(_noop_docket_lifespan)  # type: ignore[method-assign]
        FastMCP._docket_patched = True  # type: ignore[attr-defined]
        log.debug("fastmcp_docket_disabled")

def _enrich_tool_descriptions(mcp: "FastMCP") -> None:
    """Enrich tool descriptions with inline examples from TOOL_DOCS.

    Called after all tools are registered. Modifies each tool's description
    to include examples, making them visible in ListTools responses without
    requiring a separate describe() call.
    """
    from coderecon.mcp._compat import get_tools_sync
    from coderecon.mcp.docs import build_tool_description

    enriched_count = 0
    for name, tool in get_tools_sync(mcp).items():
        original_desc = tool.description or ""
        enriched_desc = build_tool_description(name, original_desc)
        if enriched_desc != original_desc:
            tool.description = enriched_desc
            enriched_count += 1

def create_mcp_server(context: "AppContext", *, dev_mode: bool = False) -> "FastMCP":
    """Create FastMCP server with all tools wired to context.

    Args:
        context: AppContext with all ops instances

    Returns:
        Configured FastMCP server ready to run
    """
    # Must patch before importing fastmcp (it configures RichHandler at import)
    _patch_rich_handler()

    import fastmcp
    from fastmcp import FastMCP

    from coderecon.mcp._compat import get_tools_sync
    from coderecon.mcp.middleware import ToolMiddleware
    from coderecon.mcp.tools import (
        checkpoint,
        diff,
        graph,
        introspection,
        recon,
        refactor,
    )

    log.info("mcp_server_creating", repo_root=str(context.repo_root))

    # Disable Docket task queue to eliminate ~15% idle CPU usage
    _patch_fastmcp_docket()

    # Configure FastMCP global settings
    fastmcp.settings.json_response = True
    # Disable FastMCP's rich tracebacks - we handle errors in middleware
    fastmcp.settings.enable_rich_tracebacks = False

    mcp = FastMCP(
        "coderecon",
        instructions="CodeRecon repository control plane for AI coding agents.",
    )

    # Add middleware for structured error handling and UX
    mcp.add_middleware(ToolMiddleware(session_manager=context.session_manager))

    # Register all tools using native FastMCP decorators
    checkpoint.register_tools(mcp, context)
    diff.register_tools(mcp, context)
    graph.register_tools(mcp, context)
    recon.register_tools(mcp, context, dev_mode=dev_mode)
    refactor.register_tools(mcp, context)
    introspection.register_tools(mcp, context)

    # Enrich tool descriptions with inline examples from TOOL_DOCS
    _enrich_tool_descriptions(mcp)

    tool_count = len(get_tools_sync(mcp))
    log.info("mcp_server_created", tool_count=tool_count)

    return mcp

def run_server(repo_root: Path, db_path: Path, tantivy_path: Path) -> None:
    """Create and run the MCP server."""
    from coderecon.config.models import LoggingConfig, LogOutputConfig
    from coderecon.core.logging import configure_logging
    from coderecon.daemon.concurrency import FreshnessGate, MutationRouter
    from coderecon.files.ops import FileOps
    from coderecon.git.ops import GitOps
    from coderecon.index.ops import IndexCoordinatorEngine
    from coderecon.lint.ops import LintOps
    from coderecon.mcp.context import AppContext
    from coderecon.mcp.session import SessionManager
    from coderecon.mutation.ops import MutationOps
    from coderecon.refactor.ops import RefactorOps
    from coderecon.testing.ops import TestOps

    # Generate session ID and log file path
    # Format: .recon/logs/YYYY-MM-DD/HHMMSS-<6-digit-hash>.log
    now = datetime.now()
    session_hash = uuid4().hex[:6]
    log_dir = repo_root / ".recon" / "logs" / now.strftime("%Y-%m-%d")
    log_file = log_dir / f"{now.strftime('%H%M%S')}-{session_hash}.log"
    session_id = f"{now.strftime('%H%M%S')}-{session_hash}"

    # Configure logging to both stderr and a file for debugging
    # Console: INFO level, no tracebacks
    # File: DEBUG level with full tracebacks
    configure_logging(
        config=LoggingConfig(
            level="DEBUG",
            outputs=[
                LogOutputConfig(destination="stderr", format="console", level="INFO"),
                LogOutputConfig(destination=str(log_file), format="json", level="DEBUG"),
            ],
        ),
    )

    log.info(
        "mcp_server_starting",
        repo_root=str(repo_root),
        db_path=str(db_path),
        tantivy_path=str(tantivy_path),
        log_file=str(log_file),
        session_id=session_id,
    )

    coordinator = IndexCoordinatorEngine(repo_root, db_path, tantivy_path)
    gate = FreshnessGate()
    router = MutationRouter(coordinator, gate)
    coordinator.set_freshness_gate(gate, "main")

    context = AppContext(
        worktree_name="main",
        repo_root=repo_root,
        git_ops=GitOps(repo_root),
        coordinator=coordinator,
        gate=gate,
        router=router,
        file_ops=FileOps(repo_root),
        mutation_ops=MutationOps(repo_root),
        refactor_ops=RefactorOps(repo_root, coordinator),
        test_ops=TestOps(repo_root, coordinator),
        lint_ops=LintOps(repo_root, coordinator),
        session_manager=SessionManager(),
    )
    mcp = create_mcp_server(context)

    log.info("mcp_server_running")
    mcp.run()
