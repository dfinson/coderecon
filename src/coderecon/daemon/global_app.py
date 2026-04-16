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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from coderecon.daemon.concurrency import FreshnessGate, MutationRouter

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from coderecon.catalog.registry import CatalogRegistry
    from coderecon.daemon.indexer import BackgroundIndexer
    from coderecon.daemon.watcher import FileWatcher
    from coderecon.index.ops import IndexCoordinatorEngine
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
    mcp_asgi_app: Any  # Built from mcp.http_app(); lifespan already started
    _mcp_lifespan_ctx: Any  # Active async context manager — used for clean shutdown


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


class GlobalDaemon:
    """Manages multiple repos behind a single Starlette app."""

    def __init__(self, registry: CatalogRegistry) -> None:
        self.registry = registry
        self._slots: dict[str, RepoSlot] = {}
        self._start_time = time.time()

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
        """
        from coderecon.config.loader import load_config
        from coderecon.daemon.indexer import BackgroundIndexer
        from coderecon.index.ops import IndexCoordinatorEngine

        config = load_config(repo_root)

        recon_dir = repo_root / ".recon"
        db_path = recon_dir / "index.db"
        tantivy_path = recon_dir / "tantivy"

        # Shared per-repo resources
        coordinator = IndexCoordinatorEngine(
            repo_root=repo_root,
            db_path=db_path,
            tantivy_path=tantivy_path,
        )
        await coordinator.load_existing()

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

        # Discover worktrees from git and activate each one
        from coderecon.git.ops import GitOps

        git_ops = GitOps(repo_root)
        try:
            worktrees = git_ops.worktrees()
        except Exception:
            # Fallback: just the main worktree
            worktrees = []

        if not worktrees:
            # Single worktree (no git worktree setup)
            wt_slot = await self._activate_worktree(
                slot, "main", repo_root, config, dev_mode=dev_mode,
            )
            slot.worktrees["main"] = wt_slot
        else:
            for wt in worktrees:
                wt_slot = await self._activate_worktree(
                    slot, wt.name, Path(wt.path), config, dev_mode=dev_mode,
                )
                slot.worktrees[wt.name] = wt_slot

        self._slots[name] = slot

        log.info(
            "repo_activated",
            name=name,
            repo_root=str(repo_root),
            worktrees=list(slot.worktrees.keys()),
        )

        return slot

    async def _activate_worktree(
        self,
        repo_slot: RepoSlot,
        wt_name: str,
        wt_root: Path,
        config: Any,
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
            stats: Any, paths: list[Path]  # noqa: ARG001
        ) -> None:
            try:
                _registry.update_last_indexed_at(_wt_root, time.time())
            except Exception as exc:
                log.warning("last_indexed_at.update_failed", error=str(exc))

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

    async def stop_all(self) -> None:
        """Stop all active repo slots."""
        for name in list(self._slots):
            await self.deactivate_repo(name)

    async def queue_startup_scans(self) -> None:
        """Queue incremental reindex for files changed while the daemon was down.

        For the main worktree: uses ``changed_since_last_index()`` (git-diff
        based) to find files changed since the last indexed commit.

        For non-main worktrees: uses ``git diff main...HEAD`` to find files
        that differ from main, then queues only those files under the worktree's
        own tag (the diff-only strategy — unchanged files fall through to the
        main worktree's entries in overlay queries).
        """
        from coderecon.git.ops import GitOps

        for name, slot in self._slots.items():
            loop = asyncio.get_event_loop()

            # Main worktree: use the coordinator's reconciler (indexed-commit based).
            main_slot = slot.worktrees.get("main")
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

            # Non-main worktrees: queue files that diff from main.
            for wt_name, wt_slot in slot.worktrees.items():
                if wt_name == "main":
                    continue
                try:
                    git_ops = GitOps(wt_slot.repo_root)
                    diff_paths = await loop.run_in_executor(
                        None, git_ops.files_changed_vs, "main"
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
                except Exception as exc:
                    log.warning(
                        "startup_scan_worktree_diff_failed",
                        repo=name,
                        worktree=wt_name,
                        error=str(exc),
                    )

    async def refresh_worktrees(self, name: str, *, dev_mode: bool = False) -> list[str]:
        """Discover and activate any git worktrees not yet known for a registered repo.

        Returns the names of newly activated worktrees.
        """
        from coderecon.config.loader import load_config
        from coderecon.git.ops import GitOps

        slot = self._slots.get(name)
        if slot is None:
            return []

        # Use the first known worktree root as the git working directory
        any_wt = next(iter(slot.worktrees.values()), None)
        if any_wt is None:
            return []

        config = load_config(any_wt.repo_root)
        git_ops = GitOps(any_wt.repo_root)
        try:
            worktrees = git_ops.worktrees()
        except Exception:
            return []

        new_names: list[str] = []
        for wt in worktrees:
            if wt.name not in slot.worktrees:
                # F2: only activate worktrees that are registered in the catalog.
                wt_path = Path(wt.path)
                if self.registry.lookup_by_path(wt_path) is None:
                    log.debug(
                        "refresh_worktrees.skip_uncatalogued",
                        repo=name, worktree=wt.name,
                    )
                    continue
                wt_slot = await self._activate_worktree(
                    slot, wt.name, wt_path, config, dev_mode=dev_mode,
                )
                slot.worktrees[wt.name] = wt_slot
                new_names.append(wt.name)

        if new_names:
            log.info("worktrees_refreshed", repo=name, added=new_names)
        return new_names

    def build_app(self, *, dev_mode: bool = False) -> Starlette:
        """Build the global Starlette application with all routes.

        Per-repo and per-worktree routes are handled dynamically so that repos
        registered after startup are reachable immediately — the Starlette app
        does not need to be rebuilt.
        """

        # --- Global routes ---

        async def health(request: Request) -> JSONResponse:
            _ = request
            return JSONResponse({
                "status": "healthy",
                "active_repos": list(self._slots.keys()),
                "uptime_seconds": round(time.time() - self._start_time, 1),
            })

        async def catalog_list(request: Request) -> JSONResponse:
            """List all registered repos and their endpoints."""
            _ = request
            repos = self.registry.list_repos()
            entries = []
            for repo in repos:
                worktrees = self.registry.list_worktrees(repo.id)  # type: ignore[arg-type]
                active = repo.name in self._slots
                slot = self._slots.get(repo.name)
                entries.append({
                    "name": repo.name,
                    "git_dir": repo.git_dir,
                    "active": active,
                    "worktrees": [
                        {
                            "name": wt.name,
                            "root_path": wt.root_path,
                            "branch": wt.branch,
                            "is_main": wt.is_main,
                            "mcp_endpoint": (
                                f"/repos/{repo.name}/worktrees/{wt.name}/mcp"
                                if slot and wt.name in slot.worktrees
                                else None
                            ),
                        }
                        for wt in worktrees
                    ],
                })
            return JSONResponse({"repositories": entries})

        async def catalog_register(request: Request) -> JSONResponse:
            """Register a new repo via POST with {"path": "/abs/path/to/repo"}."""
            body = await request.json()
            path = Path(body.get("path", ""))
            if not path.is_absolute() or not path.is_dir():
                return JSONResponse(
                    {"error": f"Invalid path: {path}"}, status_code=400,
                )

            try:
                repo, wt = self.registry.register(path)
            except Exception as exc:
                return JSONResponse(
                    {"error": f"Registration failed: {exc}"}, status_code=400,
                )

            # Auto-activate if not already active
            if repo.name not in self._slots:
                storage_dir = self.registry.get_storage_dir(repo)
                await self.activate_repo(
                    name=repo.name,
                    repo_root=Path(wt.root_path),
                    storage_dir=storage_dir,
                    repo_id=repo.id,  # type: ignore[arg-type]
                    dev_mode=dev_mode,
                )

            slot = self._slots.get(repo.name)
            mcp_endpoint = None
            if slot and wt.name in slot.worktrees:
                mcp_endpoint = f"/repos/{repo.name}/worktrees/{wt.name}/mcp"

            return JSONResponse({
                "repo": repo.name,
                "worktree": wt.name,
                "mcp_endpoint": mcp_endpoint,
            }, status_code=201)

        async def catalog_unregister(request: Request) -> JSONResponse:
            """Unregister a repo via POST with {"path": "/abs/path/to/repo"}.

            Removes from catalog DB and stops the live daemon slot (if active).
            """
            body = await request.json()
            path = Path(body.get("path", ""))
            if not path.is_absolute():
                return JSONResponse({"error": f"Invalid path: {path}"}, status_code=400)

            # Resolve repo name BEFORE removing from catalog
            repo_name = self.registry.get_repo_name_for_path(path)

            removed = self.registry.unregister(path)
            if not removed:
                return JSONResponse({"error": "Path not registered"}, status_code=404)

            deactivated = False
            if repo_name:
                deactivated = await self.deactivate_repo(repo_name)

            return JSONResponse({"removed": True, "deactivated": deactivated})

        async def repo_health(request: Request) -> JSONResponse:
            """Per-repo health check."""
            name = request.path_params["name"]
            slot = self._slots.get(name)
            if slot is None:
                return JSONResponse({"error": f"Repo '{name}' not active"}, status_code=404)
            return JSONResponse({
                "status": "healthy",
                "name": name,
                "worktrees": list(slot.worktrees.keys()),
            })

        async def repo_status(request: Request) -> JSONResponse:
            """Per-repo detailed status."""
            name = request.path_params["name"]
            slot = self._slots.get(name)
            if slot is None:
                return JSONResponse({"error": f"Repo '{name}' not active"}, status_code=404)
            indexer_status = slot.indexer.status
            return JSONResponse({
                "name": name,
                "storage_dir": str(slot.storage_dir),
                "indexer": {
                    "state": indexer_status.state.value,
                    "queue_size": indexer_status.queue_size,
                },
                "worktrees": {
                    wt_name: {
                        "repo_root": str(wt_slot.repo_root),
                        "stale": slot.gate.is_stale(wt_name),
                    }
                    for wt_name, wt_slot in slot.worktrees.items()
                },
            })

        async def repo_reindex(request: Request) -> Response:
            """Per-repo reindex trigger."""
            name = request.path_params["name"]
            slot = self._slots.get(name)
            if slot is None:
                return JSONResponse({"error": f"Repo '{name}' not active"}, status_code=404)
            for wt_name, wt_slot in slot.worktrees.items():
                slot.indexer.queue_paths(wt_name, [wt_slot.repo_root])
            return Response(status_code=202)

        async def repo_refresh_worktrees(request: Request) -> JSONResponse:
            """Discover and activate new git worktrees for a repo at runtime."""
            name = request.path_params["name"]
            if name not in self._slots:
                return JSONResponse({"error": f"Repo '{name}' not active"}, status_code=404)
            new_wts = await self.refresh_worktrees(name, dev_mode=dev_mode)
            return JSONResponse({"added_worktrees": new_wts})

        # --- Build route tree ---
        # Per-repo management routes use Starlette path params so they resolve
        # dynamically at call time — no rebuild needed when repos are added.
        # MCP endpoints are dispatched by _DynamicMcpRouter, which looks up
        # self._slots on every request.

        routes = [
            Route("/health", health, methods=["GET"]),
            Route("/catalog", catalog_list, methods=["GET"]),
            Route("/catalog/register", catalog_register, methods=["POST"]),
            Route("/catalog/unregister", catalog_unregister, methods=["POST"]),
            Route("/repos/{name}/health", repo_health, methods=["GET"]),
            Route("/repos/{name}/status", repo_status, methods=["GET"]),
            Route("/repos/{name}/reindex", repo_reindex, methods=["POST"]),
            Route("/repos/{name}/refresh-worktrees", repo_refresh_worktrees, methods=["POST"]),
            # Catch-all for /repos/{name}/worktrees/{wt}/mcp/...
            _DynamicMcpRouter(self),
        ]

        @asynccontextmanager
        async def lifespan(_app: Starlette) -> AsyncIterator[None]:
            # MCP sub-app lifespans are already running (started in _activate_worktree).
            yield
            await self.stop_all()

        return Starlette(routes=routes, lifespan=lifespan)


# ---------------------------------------------------------------------------
# Dynamic MCP dispatcher
# ---------------------------------------------------------------------------

async def _asgi_not_found(scope: Any, receive: Any, send: Any) -> None:  # noqa: ARG001
    await send({
        "type": "http.response.start",
        "status": 404,
        "headers": [(b"content-type", b"application/json")],
    })
    await send({
        "type": "http.response.body",
        "body": b'{"error":"not found"}',
        "more_body": False,
    })


class _DynamicMcpRouter:
    """Starlette BaseRoute that dispatches /repos/{name}/worktrees/{wt}/mcp/... requests.

    Implements the three-method Starlette BaseRoute protocol (matches / handle /
    url_path_for) so it can be dropped directly into the Starlette route list.
    Looks up the live WorktreeSlot at request time, so repos and worktrees
    activated after build_app() are reachable immediately.
    """

    def __init__(self, daemon: GlobalDaemon) -> None:
        self._daemon = daemon

    # ------------------------------------------------------------------
    # Starlette BaseRoute protocol
    # ------------------------------------------------------------------

    def matches(self, scope: Any) -> tuple[Any, Any]:
        from starlette.routing import Match

        if scope["type"] not in ("http", "websocket"):
            return Match.NONE, {}

        path: str = scope.get("path", "")
        parts = path.lstrip("/").split("/", 4)
        # Expected: ["repos", name, "worktrees", wt_name, ...]
        if (
            len(parts) >= 4
            and parts[0] == "repos"
            and parts[2] == "worktrees"
        ):
            return Match.FULL, {}
        return Match.NONE, {}

    def url_path_for(self, name: str, /, **path_params: Any) -> Any:
        from starlette.routing import NoMatchFound

        raise NoMatchFound(name, path_params)

    async def handle(self, scope: Any, receive: Any, send: Any) -> None:
        path: str = scope.get("path", "")
        parts = path.lstrip("/").split("/", 4)
        repo_name = parts[1]
        wt_name = parts[3]

        slot = self._daemon._slots.get(repo_name)
        if slot is None:
            await _asgi_not_found(scope, receive, send)
            return

        wt_slot = slot.worktrees.get(wt_name)
        if wt_slot is None:
            await _asgi_not_found(scope, receive, send)
            return

        # Strip /repos/{name}/worktrees/{wt} — leave /mcp/... for the MCP app.
        prefix = f"/repos/{repo_name}/worktrees/{wt_name}"
        new_path = path[len(prefix):] or "/"
        new_scope = dict(scope)
        new_scope["path"] = new_path
        new_scope["root_path"] = scope.get("root_path", "") + prefix

        await wt_slot.mcp_asgi_app(new_scope, receive, send)

    # Allow use as a standalone ASGI app too (matches BaseRoute.__call__)
    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        from starlette.routing import Match

        match, _ = self.matches(scope)
        if match == Match.NONE:
            await _asgi_not_found(scope, receive, send)
            return
        await self.handle(scope, receive, send)

