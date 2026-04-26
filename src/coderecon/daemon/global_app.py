"""Global Starlette application for multi-repo daemon.

Serves a catalog of repos, each with worktree-level MCP endpoints mounted at
/repos/{name}/worktrees/{worktree}/mcp.

Routing is fully dynamic: new repos and worktrees activated at runtime are
immediately reachable without rebuilding the Starlette app.  MCP sub-app
lifespans are started/stopped per WorktreeSlot, not in the root app lifespan.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from starlette.applications import Starlette

from coderecon.daemon.concurrency import FreshnessGate, MutationRouter
from coderecon.git.errors import GitError

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from coderecon.catalog.registry import CatalogRegistry
    from coderecon.config.models import CodeReconConfig
    from coderecon.daemon.indexer import BackgroundIndexer
    from coderecon.daemon.watcher import FileWatcher
    from coderecon.index.ops import IndexCoordinatorEngine, IndexStats
    from coderecon.mcp.context import AppContext
    from coderecon.mcp.session import SessionManager

log = structlog.get_logger(__name__)


@dataclass
class WorktreeSlot:
    """Runtime state for a single worktree within a repo."""

    name: str
    repo_root: Path
    watcher: FileWatcher
    app_ctx: AppContext
    session_manager: SessionManager
    mcp: FastMCP
    mcp_asgi_app: Starlette
    _mcp_lifespan_ctx: contextlib.AbstractAsyncContextManager[None]
    activated_at: float = field(default_factory=time.time)
    last_request_at: float = field(default_factory=time.time)


class RepoSlot:
    """Runtime state for a single registered repository.

    Owns the shared index coordinator, freshness gate, mutation router,
    and background indexer.  Each worktree gets its own WorktreeSlot.
    """

    __slots__ = (
        "name",
        "repo_id",
        "storage_dir",
        "coordinator",
        "gate",
        "router",
        "indexer",
        "worktrees",
    )

    def __init__(
        self,
        name: str,
        repo_id: int,
        storage_dir: Path,
        coordinator: IndexCoordinatorEngine,
        gate: FreshnessGate,
        router: MutationRouter,
        indexer: BackgroundIndexer,
    ) -> None:
        self.name = name
        self.repo_id = repo_id
        self.storage_dir = storage_dir
        self.coordinator = coordinator
        self.gate = gate
        self.router = router
        self.indexer = indexer
        self.worktrees: dict[str, WorktreeSlot] = {}


# Default inotify watch ceiling.  The real limit comes from
# /proc/sys/fs/inotify/max_user_watches but 200_000 is a safe budget to
# stay well below the typical 524288 default and leave room for other procs.
_DEFAULT_WATCH_CEILING = 200_000


class GlobalDaemon:
    """Manages multiple repos behind a single Starlette app."""

    def __init__(
        self,
        registry: CatalogRegistry,
        *,
        watch_ceiling: int = _DEFAULT_WATCH_CEILING,
    ) -> None:
        self.registry = registry
        self._slots: dict[str, RepoSlot] = {}
        self._start_time = time.time()
        self._watch_ceiling = watch_ceiling
        self._eviction_task: asyncio.Task[None] | None = None

    @property
    def slot_names(self) -> list[str]:
        return list(self._slots.keys())

    def get_slot(self, name: str) -> RepoSlot | None:
        return self._slots.get(name)

    async def activate_repo(
        self,
        name: str,
        repo_root: Path,
        storage_dir: Path,
        repo_id: int,
        *,
        dev_mode: bool = False,
    ) -> RepoSlot:
        """Activate a registered repo: create shared coordinator + per-worktree slots.

        Loads the existing index (no full reindex) so startup is fast.

        Only the **main** worktree is eagerly activated (watcher + MCP).
        Non-main worktrees get their freshness gate registered and diff-based
        reindex queued (so their changed files are indexed immediately) but are
        NOT fully activated (no watcher/MCP) until a client actually connects
        (lazy activation in ``_DynamicMcpRouter``).  This avoids inotify/memory
        exhaustion when hundreds of worktrees are registered.
        """
        from coderecon.config.loader import load_config
        from coderecon.daemon.indexer import BackgroundIndexer
        from coderecon.index.ops import IndexCoordinatorEngine

        config = load_config(repo_root)

        recon_dir = repo_root / ".recon"
        recon_dir.mkdir(exist_ok=True)
        db_path = recon_dir / "index.db"
        tantivy_path = recon_dir / "tantivy"

        # Shared per-repo resources
        coordinator = IndexCoordinatorEngine(
            repo_root=repo_root,
            db_path=db_path,
            tantivy_path=tantivy_path,
            busy_timeout_ms=config.database.busy_timeout_ms,
        )
        await coordinator.load_existing()

        # Backfill any missing derived signals (e.g. SPLADE vectors for
        # repos indexed before the signal was introduced, or after a model
        # version bump).  Cheap no-op when everything is consistent.
        coordinator.backfill_missing_signals()

        gate = FreshnessGate()
        router = MutationRouter(coordinator, gate)
        indexer = BackgroundIndexer(
            coordinator=coordinator,
            gate=gate,
            config=config.indexer,
        )
        indexer.start()

        slot = RepoSlot(
            name=name,
            repo_id=repo_id,
            storage_dir=storage_dir,
            coordinator=coordinator,
            gate=gate,
            router=router,
            indexer=indexer,
        )

        # Discover worktrees from git
        from coderecon.git.ops import GitOps

        git_ops = GitOps(repo_root)
        try:
            worktrees = git_ops.worktrees()
        except GitError:
            log.debug("worktree_discovery_failed", exc_info=True)
            worktrees = []

        # Resolve the repo's default branch once for all worktree diffs.
        base_branch = git_ops.default_branch()

        if not worktrees:
            # Single worktree (no git worktree setup) — always activate main
            wt_slot = await self._activate_worktree(
                slot, "main", repo_root, config, dev_mode=dev_mode,
            )
            slot.worktrees["main"] = wt_slot
        else:
            for wt in worktrees:
                if wt.is_main:
                    # Main worktree: eagerly activate
                    wt_slot = await self._activate_worktree(
                        slot, wt.name, Path(wt.path), config, dev_mode=dev_mode,
                    )
                    slot.worktrees[wt.name] = wt_slot
                else:
                    # Non-main: register freshness gate (creates DB row) but
                    # do NOT create watcher/MCP — lazy activation on first request.
                    wt_path = Path(wt.path)
                    coordinator.set_freshness_gate(
                        gate, wt.name, worktree_root=str(wt_path),
                    )

                    # Queue diff-based reindex so the worktree's changed
                    # files are indexed immediately after registration.
                    try:
                        wt_git = GitOps(wt_path)
                        loop = asyncio.get_event_loop()
                        diff_paths = await loop.run_in_executor(
                            None, wt_git.files_changed_vs, base_branch,
                        )
                        if diff_paths:
                            abs_paths = [wt_path / p for p in diff_paths]
                            indexer.queue_paths(wt.name, abs_paths)
                            log.info(
                                "activate_repo.queued_worktree_diff",
                                repo=name,
                                worktree=wt.name,
                                changed_files=len(diff_paths),
                            )
                    except (GitError, OSError) as exc:
                        log.warning(
                            "activate_repo.worktree_diff_failed",
                            repo=name,
                            worktree=wt.name,
                            error=str(exc),
                            exc_info=True,
                        )

        self._slots[name] = slot

        log.info(
            "repo_activated",
            name=name,
            repo_root=str(repo_root),
            worktrees=list(slot.worktrees.keys()),
            lazy_worktrees=len(worktrees) - len(slot.worktrees) if worktrees else 0,
        )

        return slot

    async def _activate_worktree(
        self,
        repo_slot: RepoSlot,
        wt_name: str,
        wt_root: Path,
        config: CodeReconConfig,
        *,
        dev_mode: bool = False,
    ) -> WorktreeSlot:
        """Activate a single worktree within a repo slot."""
        from coderecon.daemon.analysis_pipeline import AnalysisPipeline
        from coderecon.daemon.watcher import FileWatcher
        from coderecon.files.ops import FileOps
        from coderecon.git.ops import GitOps
        from coderecon.lint.ops import LintOps
        from coderecon.mcp.context import AppContext
        from coderecon.mcp.server import create_mcp_server
        from coderecon.mcp.session import SessionManager
        from coderecon.mutation.ops import MutationOps
        from coderecon.refactor.ops import RefactorOps
        from coderecon.testing.ops import TestOps

        # Per-worktree ops
        git_ops = GitOps(wt_root)
        file_ops = FileOps(wt_root)
        mutation_ops = MutationOps(wt_root)
        refactor_ops = RefactorOps(wt_root, repo_slot.coordinator)
        test_ops = TestOps(wt_root, repo_slot.coordinator)
        lint_ops = LintOps(wt_root, repo_slot.coordinator)
        session_manager = SessionManager(config.timeouts)

        # Inject freshness gate into coordinator for this worktree.
        # Pass the worktree's actual checkout path so the Worktree row gets
        # the correct root_path (not always the main repo root).
        repo_slot.coordinator.set_freshness_gate(
            repo_slot.gate, wt_name, worktree_root=str(wt_root)
        )

        app_ctx = AppContext(
            worktree_name=wt_name,
            repo_root=wt_root,
            git_ops=git_ops,
            coordinator=repo_slot.coordinator,
            gate=repo_slot.gate,
            router=repo_slot.router,
            file_ops=file_ops,
            mutation_ops=mutation_ops,
            refactor_ops=refactor_ops,
            test_ops=test_ops,
            lint_ops=lint_ops,
            session_manager=session_manager,
        )

        mcp = create_mcp_server(app_ctx, dev_mode=dev_mode)

        # Build the ASGI app for this worktree and start its lifespan immediately.
        # This means new worktrees activated after build_app() is called are instantly
        # reachable via _DynamicMcpRouter without rebuilding the Starlette app.
        mcp_asgi_app = mcp.http_app(path="/mcp", transport="streamable-http")
        mcp_lifespan_ctx = mcp_asgi_app.router.lifespan_context(mcp_asgi_app)
        await mcp_lifespan_ctx.__aenter__()

        # File watcher per worktree, routing changes to shared indexer
        watcher = FileWatcher(
            repo_root=wt_root,
            on_change=lambda paths, _wt=wt_name: repo_slot.indexer.queue_paths(_wt, paths),
            poll_interval=config.server.poll_interval_sec,
        )
        await watcher.start()

        # Wire analysis pipeline
        pipeline = AnalysisPipeline(
            coordinator=repo_slot.coordinator,
            lint_ops=lint_ops,
            test_ops=test_ops,
            repo_root=wt_root,
        )
        repo_slot.indexer.add_on_complete(pipeline.on_index_complete)

        # F3: write last_indexed_at into the catalog after each successful flush.
        _registry = self.registry
        _wt_root = wt_root

        async def _update_last_indexed_at(
            stats: IndexStats, paths: list[Path]  # noqa: ARG001
        ) -> None:
            try:
                _registry.update_last_indexed_at(_wt_root, time.time())
            except (OSError, RuntimeError) as exc:
                log.warning("last_indexed_at.update_failed", error=str(exc), exc_info=True)

        repo_slot.indexer.add_on_complete(_update_last_indexed_at)

        return WorktreeSlot(
            name=wt_name,
            repo_root=wt_root,
            watcher=watcher,
            app_ctx=app_ctx,
            session_manager=session_manager,
            mcp=mcp,
            mcp_asgi_app=mcp_asgi_app,
            _mcp_lifespan_ctx=mcp_lifespan_ctx,
        )

    async def _stop_worktree_slot(self, wt_slot: WorktreeSlot) -> None:
        """Stop a single worktree slot: file watcher + MCP app lifespan."""
        await wt_slot.watcher.stop()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                wt_slot._mcp_lifespan_ctx.__aexit__(None, None, None),
                timeout=2.0,
            )

    async def lazy_activate_worktree(
        self,
        repo_name: str,
        wt_name: str,
        *,
        dev_mode: bool = False,
    ) -> WorktreeSlot | None:
        """Activate a worktree on-demand (first MCP connection).

        Returns the new WorktreeSlot, or None if the worktree can't be found
        in the catalog or the repo slot doesn't exist.
        """
        from coderecon.config.loader import load_config
        from coderecon.daemon.watcher import FileWatcher

        slot = self._slots.get(repo_name)
        if slot is None:
            return None

        # Already activated (race between concurrent requests)
        if wt_name in slot.worktrees:
            return slot.worktrees[wt_name]

        # Look up worktree path from catalog
        wt_entry = self.registry.lookup_worktree(slot.repo_id, wt_name)
        if wt_entry is None:
            return None

        wt_path = Path(wt_entry.root_path)
        if not wt_path.is_dir():
            log.warning("lazy_activate.missing_path", repo=repo_name, worktree=wt_name, path=str(wt_path))
            return None

        # Ceiling check: estimate inotify watches and refuse if over budget.
        estimated = FileWatcher.estimate_watch_count(wt_path)
        current = self._current_watch_count()
        if current + estimated > self._watch_ceiling:
            log.warning(
                "lazy_activate.ceiling_exceeded",
                repo=repo_name,
                worktree=wt_name,
                current=current,
                estimated=estimated,
                ceiling=self._watch_ceiling,
            )
            return None

        config = load_config(wt_path)
        wt_slot = await self._activate_worktree(
            slot, wt_name, wt_path, config, dev_mode=dev_mode,
        )
        slot.worktrees[wt_name] = wt_slot

        log.info(
            "lazy_activate.activated",
            repo=repo_name,
            worktree=wt_name,
            watches=wt_slot.watcher.watch_count,
        )
        return wt_slot

    async def deactivate_repo(self, name: str) -> bool:
        """Stop and remove a repo slot."""
        slot = self._slots.pop(name, None)
        if slot is None:
            return False

        # Stop all worktree watchers and MCP lifespans
        for wt_slot in slot.worktrees.values():
            await self._stop_worktree_slot(wt_slot)

        # Stop shared indexer
        await slot.indexer.stop()

        log.info("repo_deactivated", name=name)
        return True

    async def lazy_activate_repo(
        self,
        repo_name: str,
        *,
        dev_mode: bool = False,
    ) -> RepoSlot | None:
        """Activate a repo on-demand (first MCP connection).

        Looks up the repo in the catalog, loads its index, and creates
        the RepoSlot.  Returns None if the repo isn't registered.
        """
        if repo_name in self._slots:
            return self._slots[repo_name]

        result = self.registry.lookup_by_name(repo_name)
        if result is None:
            return None
        repo, main_wt = result

        repo_root = Path(main_wt.root_path)
        if not repo_root.is_dir():
            log.warning("lazy_activate_repo.missing_path", repo=repo_name, path=str(repo_root))
            return None

        storage_dir = self.registry.get_storage_dir(repo)
        try:
            slot = await self.activate_repo(
                name=repo_name,
                repo_root=repo_root,
                storage_dir=storage_dir,
                repo_id=repo.id,  # type: ignore[arg-type]  # repo.id is non-None after DB persist
                dev_mode=dev_mode,
            )
            log.info("lazy_activate_repo.activated", repo=repo_name)
            return slot
        except Exception as exc:
            log.warning("lazy_activate_repo.failed", repo=repo_name, error=str(exc))
            return None

    async def stop_all(self) -> None:
        """Stop all active repo slots."""
        # Cancel eviction loop first
        if self._eviction_task is not None and not self._eviction_task.done():
            self._eviction_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._eviction_task
            self._eviction_task = None

        for name in list(self._slots):
            await self.deactivate_repo(name)

    def _current_watch_count(self) -> int:
        """Sum of inotify watches across all active worktree watchers."""
        total = 0
        for slot in self._slots.values():
            for wt_slot in slot.worktrees.values():
                total += wt_slot.watcher.watch_count
        return total

    async def _eviction_loop(self, idle_timeout: float) -> None:
        """Periodically evict idle non-main WorktreeSlots.

        Runs every ``idle_timeout / 2`` seconds.  Only non-main worktrees
        that have been idle longer than ``idle_timeout`` are torn down.
        They can be lazily re-activated on the next MCP request.
        """
        interval = idle_timeout / 2
        try:
            while True:
                await asyncio.sleep(interval)
                now = time.time()
                for slot in list(self._slots.values()):
                    for wt_name in list(slot.worktrees):
                        if wt_name == "main":
                            continue
                        wt_slot = slot.worktrees[wt_name]
                        idle_sec = now - wt_slot.last_request_at
                        if idle_sec >= idle_timeout:
                            log.info(
                                "eviction.idle_teardown",
                                repo=slot.name,
                                worktree=wt_name,
                                idle_sec=round(idle_sec, 1),
                            )
                            await self._stop_worktree_slot(wt_slot)
                            del slot.worktrees[wt_name]
        except asyncio.CancelledError:
            pass

    def start_eviction_loop(self, idle_timeout: float) -> None:
        """Start the idle-eviction background task.

        Args:
            idle_timeout: Seconds of inactivity before a non-main worktree
                          is torn down.  Pass 0 to disable.
        """
        if idle_timeout <= 0:
            return
        if self._eviction_task is not None and not self._eviction_task.done():
            return
        self._eviction_task = asyncio.create_task(self._eviction_loop(idle_timeout))

    async def queue_startup_scans(self) -> None:
        """Queue incremental reindex for files changed while the daemon was down.

        For the main worktree: uses ``changed_since_last_index()`` (git-diff
        based) to find files changed since the last indexed commit.

        For non-main worktrees: diffs against the repo's default branch to
        find files that differ, then queues only those files under the
        worktree's own tag (the diff-only strategy — unchanged files fall
        through to the main worktree's entries in overlay queries).
        """
        from coderecon.git.ops import GitOps

        for name, slot in self._slots.items():
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
                        None, git_ops.files_changed_vs, base_branch or "main"
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

    async def refresh_worktrees(self, name: str, *, dev_mode: bool = False) -> list[str]:
        """Discover and register any git worktrees not yet known for a repo.

        New worktrees get their freshness gate registered (creates DB row)
        and their diff files queued for reindex into the shared ``index.db``.
        They are NOT eagerly activated (no watcher/MCP) — that happens lazily
        on first MCP connection via ``_DynamicMcpRouter``.

        Returns the names of newly discovered worktrees.
        """
        from coderecon.git.ops import GitOps

        slot = self._slots.get(name)
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
            if self.registry.lookup_by_path(wt_path) is None:
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
                    None, wt_git.files_changed_vs, base_branch
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

    def build_app(self, *, dev_mode: bool = False) -> Starlette:
        """Build the global Starlette application with all routes.

        Per-repo and per-worktree routes are handled dynamically so that repos
        registered after startup are reachable immediately — the Starlette app
        does not need to be rebuilt.
        """
        from coderecon.daemon.global_routes import build_global_app

        return build_global_app(self, dev_mode=dev_mode)

