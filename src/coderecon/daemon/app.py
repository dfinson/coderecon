"""Starlette application factory for single-repo daemon."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.routing import BaseRoute, Mount

from coderecon.daemon.concurrency import FreshnessGate, MutationRouter
from coderecon.daemon.middleware import RepoHeaderMiddleware
from coderecon.daemon.routes import create_routes

if TYPE_CHECKING:
    from coderecon.daemon.lifecycle import ServerController
    from coderecon.index.ops import IndexCoordinatorEngine


def create_app(
    controller: ServerController,
    repo_root: Path,
    coordinator: IndexCoordinatorEngine,
    *,
    dev_mode: bool = False,
) -> Starlette:
    """Create the Starlette application with MCP server mounted."""
    from coderecon.files.ops import FileOps
    from coderecon.git.ops import GitOps
    from coderecon.lint.ops import LintOps
    from coderecon.mcp.context import AppContext
    from coderecon.mcp.server import create_mcp_server
    from coderecon.mcp.session import SessionManager
    from coderecon.mutation.ops import MutationOps
    from coderecon.refactor.ops import RefactorOps
    from coderecon.testing.ops import TestOps

    routes: list[BaseRoute] = list(create_routes(controller))

    coderecon_dir = repo_root / ".recon"

    # Single-repo mode: one worktree named "main"
    gate = FreshnessGate()
    router = MutationRouter(coordinator, gate)
    coordinator.set_freshness_gate(gate, "main")

    git_ops = GitOps(repo_root)
    file_ops = FileOps(repo_root)
    mutation_ops = MutationOps(repo_root)
    refactor_ops = RefactorOps(repo_root, coordinator)
    test_ops = TestOps(repo_root, coordinator)
    lint_ops = LintOps(repo_root, coordinator)
    session_manager = SessionManager()

    context = AppContext(
        worktree_name="main",
        repo_root=repo_root,
        git_ops=git_ops,
        coordinator=coordinator,
        gate=gate,
        router=router,
        file_ops=file_ops,
        mutation_ops=mutation_ops,
        refactor_ops=refactor_ops,
        test_ops=test_ops,
        lint_ops=lint_ops,
        session_manager=session_manager,
    )
    mcp = create_mcp_server(context, dev_mode=dev_mode)
    mcp_app = mcp.http_app(path="/mcp", transport="streamable-http")
    routes.append(Mount("/", app=mcp_app))

    # Wire background analysis pipeline (tier 1 lint + tier 2 tests)
    from coderecon.daemon.analysis_pipeline import AnalysisPipeline

    pipeline = AnalysisPipeline(
        coordinator=coordinator,
        lint_ops=context.lint_ops,
        test_ops=context.test_ops,
        repo_root=repo_root,
    )
    controller.indexer.add_on_complete(pipeline.on_index_complete)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        yield
        # Controller stop is handled in run_server finally block
        # to ensure it runs even if lifespan exit times out

    @asynccontextmanager
    async def mcp_lifespan_with_timeout(app: Starlette) -> AsyncIterator[None]:
        """Wrap MCP lifespan with timeout to prevent hanging on shutdown."""
        async with mcp_app.lifespan(app):
            yield
        # MCP cleanup happens when exiting the context manager
        # This is wrapped in timeout in combined_lifespan

    @asynccontextmanager
    async def combined_lifespan(app: Starlette) -> AsyncIterator[None]:
        # Enter MCP lifespan
        async with mcp_app.lifespan(app):
            yield
        # MCP lifespan exit handles stream cleanup - timeout prevents hanging
        # on stuck connections during shutdown

    app = Starlette(
        routes=routes,
        lifespan=combined_lifespan,
    )

    # Add middleware to inject repo header into responses
    app.add_middleware(RepoHeaderMiddleware, repo_root=repo_root)

    return app
