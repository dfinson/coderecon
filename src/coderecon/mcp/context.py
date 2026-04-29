"""Application context for MCP handlers.

Single object passed to all tool handlers with access to ops classes.
Constructed per-worktree by the daemon layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coderecon.daemon.concurrency import FreshnessGate, MutationRouter
    from coderecon.adapters.files.ops import FileOps
    from coderecon.adapters.git.ops import GitOps
    from coderecon.index.ops import IndexCoordinatorEngine
    from coderecon.lint.ops import LintOps
    from coderecon.mcp.session import SessionManager
    from coderecon.adapters.mutation.ops import MutationOps
    from coderecon.refactor.ops import RefactorOps
    from coderecon.testing.ops import TestOps

@dataclass
class AppContext:
    """Context object passed to all MCP tool handlers.

    Provides access to all ops classes and shared state.
    One instance per worktree — constructed by the daemon layer.
    """

    worktree_name: str
    repo_root: Path
    git_ops: GitOps
    coordinator: IndexCoordinatorEngine
    gate: FreshnessGate
    router: MutationRouter
    file_ops: FileOps
    mutation_ops: MutationOps
    refactor_ops: RefactorOps
    test_ops: TestOps
    lint_ops: LintOps
    session_manager: SessionManager

    @classmethod
    def standalone(
        cls,
        repo_root: Path,
        db_path: Path,
        tantivy_path: Path,
        *,
        worktree_name: str = "main",
    ) -> AppContext:
        """Construct a self-contained context for scripts and lab code.

        Creates all ops classes and concurrency primitives from scratch.
        Not for use inside the daemon — the daemon builds these per-worktree.
        """
        from coderecon.daemon.concurrency import FreshnessGate, MutationRouter
        from coderecon.adapters.files.ops import FileOps
        from coderecon.adapters.git.ops import GitOps
        from coderecon.index.ops import IndexCoordinatorEngine
        from coderecon.lint.ops import LintOps
        from coderecon.mcp.session import SessionManager
        from coderecon.adapters.mutation.ops import MutationOps
        from coderecon.refactor.ops import RefactorOps
        from coderecon.testing.ops import TestOps

        coordinator = IndexCoordinatorEngine(repo_root, db_path, tantivy_path)
        gate = FreshnessGate()
        router = MutationRouter(coordinator, gate)
        coordinator.set_freshness_gate(gate, worktree_name)

        return cls(
            worktree_name=worktree_name,
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
