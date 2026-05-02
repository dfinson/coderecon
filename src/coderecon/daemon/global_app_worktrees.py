"""Extracted worktree helpers for GlobalDaemon.

Standalone async functions that operate on a GlobalDaemon instance,
split out to keep global_app.py under 500 LOC.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from starlette.applications import Starlette

from coderecon.adapters.git.errors import GitError

if TYPE_CHECKING:
    from coderecon.daemon.global_app import GlobalDaemon

log = structlog.get_logger(__name__)


async def queue_startup_scans(daemon: GlobalDaemon) -> None:
    """Queue incremental reindex for files changed while the daemon was down.
    For the main worktree: uses ``changed_since_last_index()`` (git-diff
    based) to find files changed since the last indexed commit.
    For non-main worktrees: diffs against the repo's default branch to
    find files that differ, then queues only those files under the
    worktree's own tag (the diff-only strategy — unchanged files fall
    through to the main worktree's entries in overlay queries).
    """
    from coderecon.adapters.git.ops import GitOps

    for name, slot in daemon._slots.items():
        loop = asyncio.get_event_loop()

        # Resolve default branch once per repo.
        main_slot = slot.worktrees.get("main")
        base_branch: str | None = None
        if main_slot is not None:
            try:
                _main_git = GitOps(main_slot.repo_root)
                base_branch = _main_git.default_branch()
            except GitError:
                log.debug("default_branch_resolution_failed", exc_info=True)
                base_branch = "main"

        # Main worktree: use the coordinator's reconciler (indexed-commit based).
        if main_slot is not None:
            changed = await loop.run_in_executor(
                None, slot.coordinator.changed_since_last_index
            )
            if changed:
                slot.indexer.queue_paths("main", changed)
                log.info(
                    "startup_scan_queued",
                    repo=name,
                    worktree="main",
                    changed_files=len(changed),
                )
            else:
                log.debug("startup_scan_clean", repo=name, worktree="main")

        # Non-main worktrees: queue files that diff from default branch.
        for wt_name, wt_slot in slot.worktrees.items():
            if wt_name == "main":
                continue
            try:
                git_ops = GitOps(wt_slot.repo_root)
                diff_paths = await loop.run_in_executor(
                    None, git_ops.files_changed_from, base_branch or "main"
                )
                if diff_paths:
                    abs_paths = [wt_slot.repo_root / p for p in diff_paths]
                    slot.indexer.queue_paths(wt_name, abs_paths)
                    log.info(
                        "startup_scan_queued",
                        repo=name,
                        worktree=wt_name,
                        changed_files=len(diff_paths),
                    )
                else:
                    log.debug("startup_scan_clean", repo=name, worktree=wt_name)
            except (GitError, OSError) as exc:
                log.warning(
                    "startup_scan_worktree_diff_failed",
                    repo=name,
                    worktree=wt_name,
                    error=str(exc),
                    exc_info=True,
                )


async def refresh_worktrees(
    daemon: GlobalDaemon, name: str, *, dev_mode: bool = False
) -> list[str]:
    """Discover and register any git worktrees not yet known for a repo.
    New worktrees get their freshness gate registered (creates DB row)
    and their diff files queued for reindex into the shared ``index.db``.
    They are NOT eagerly activated (no watcher/MCP) — that happens lazily
    on first MCP connection via ``_DynamicMcpRouter``.
    Returns the names of newly discovered worktrees.
    """
    from coderecon.adapters.git.ops import GitOps

    slot = daemon._slots.get(name)
    if slot is None:
        return []

    # Use the first known worktree root as the git working directory
    any_wt = next(iter(slot.worktrees.values()), None)
    if any_wt is None:
        return []

    git_ops = GitOps(any_wt.repo_root)
    try:
        worktrees = git_ops.worktrees()
    except GitError:
        log.debug("refresh_worktrees_failed", exc_info=True)
        return []

    base_branch = git_ops.default_branch()
    new_names: list[str] = []
    for wt in worktrees:
        # Skip already-activated AND already-known (freshness gate set)
        if wt.name in slot.worktrees:
            continue

        # F2: only accept worktrees that are registered in the catalog.
        wt_path = Path(wt.path)
        if daemon.registry.lookup_by_path(wt_path) is None:
            log.debug(
                "refresh_worktrees.skip_uncatalogued",
                repo=name, worktree=wt.name,
            )
            continue

        # Register freshness gate (creates DB row) without full activation.
        slot.coordinator.set_freshness_gate(
            slot.gate, wt.name, worktree_root=str(wt_path),
        )
        new_names.append(wt.name)

        # Queue diff-only reindex: files that differ from default branch.
        try:
            loop = asyncio.get_event_loop()
            wt_git = GitOps(wt_path)
            diff_paths = await loop.run_in_executor(
                None, wt_git.files_changed_from, base_branch
            )
            if diff_paths:
                abs_paths = [wt_path / p for p in diff_paths]
                slot.indexer.queue_paths(wt.name, abs_paths)
                log.info(
                    "refresh_worktrees.queued_diff",
                    repo=name,
                    worktree=wt.name,
                    changed_files=len(diff_paths),
                )
        except (GitError, OSError) as exc:
            log.warning(
                "refresh_worktrees.diff_scan_failed",
                repo=name,
                worktree=wt.name,
                error=str(exc),
                exc_info=True,
            )

    if new_names:
        log.info("worktrees_refreshed", repo=name, added=new_names)
    return new_names


def build_app(daemon: GlobalDaemon, *, dev_mode: bool = False) -> Starlette:
    """Build the global Starlette application with all routes.
    Per-repo and per-worktree routes are handled dynamically so that repos
    registered after startup are reachable immediately — the Starlette app
    does not need to be rebuilt.
    """
    from coderecon.daemon.global_routes import build_global_app

    return build_global_app(daemon, dev_mode=dev_mode)
