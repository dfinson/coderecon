"""HTTP route handlers and ASGI routing for the global multi-repo daemon.

Extracted from ``global_app.py`` to keep file sizes manageable.  The main
entry point is :func:`build_global_app` which wires Starlette routes to a
running :class:`~coderecon.daemon.global_app.GlobalDaemon`.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from coderecon.git.errors import GitError

if TYPE_CHECKING:
    from coderecon.daemon.global_app import GlobalDaemon

# Route handlers — module-level functions taking (daemon, ..., request)

async def _health(daemon: GlobalDaemon, request: Request) -> JSONResponse:
    _ = request
    return JSONResponse({
        "status": "healthy",
        "active_repos": list(daemon._slots.keys()),
        "uptime_seconds": round(time.time() - daemon._start_time, 1),
    })

async def _catalog_list(daemon: GlobalDaemon, request: Request) -> JSONResponse:
    """List all registered repos and their endpoints."""
    _ = request
    repos = daemon.registry.list_repos()
    entries = []
    for repo in repos:
        worktrees = daemon.registry.list_worktrees(repo.id)  # type: ignore[arg-type]  # repo.id is non-None after DB query
        active = repo.name in daemon._slots
        slot = daemon._slots.get(repo.name)
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
                    "activated": bool(slot and wt.name in slot.worktrees),
                    "mcp_endpoint": (
                        f"/repos/{repo.name}/worktrees/{wt.name}/mcp"
                    ),
                }
                for wt in worktrees
            ],
        })
    return JSONResponse({"repositories": entries})

async def _catalog_register(
    daemon: GlobalDaemon, dev_mode: bool, request: Request,
) -> JSONResponse:
    """Register a new repo via POST with {"path": "/abs/path/to/repo"}."""
    body = await request.json()
    path = Path(body.get("path", ""))
    if not path.is_absolute() or not path.is_dir():
        return JSONResponse(
            {"error": f"Invalid path: {path}"}, status_code=400,
        )

    try:
        repo, wt = daemon.registry.register(path)
    except (GitError, OSError, ValueError) as exc:
        return JSONResponse(
            {"error": f"Registration failed: {exc}"}, status_code=400,
        )

    # Auto-activate if not already active
    if repo.name not in daemon._slots:
        storage_dir = daemon.registry.get_storage_dir(repo)
        await daemon.activate_repo(
            name=repo.name,
            repo_root=Path(wt.root_path),
            storage_dir=storage_dir,
            repo_id=repo.id,  # type: ignore[arg-type]  # repo.id is non-None after DB persist
            dev_mode=dev_mode,
        )

    slot = daemon._slots.get(repo.name)
    mcp_endpoint = None
    if slot and wt.name in slot.worktrees:
        mcp_endpoint = f"/repos/{repo.name}/worktrees/{wt.name}/mcp"

    return JSONResponse({
        "repo": repo.name,
        "worktree": wt.name,
        "mcp_endpoint": mcp_endpoint,
    }, status_code=201)

async def _catalog_unregister(daemon: GlobalDaemon, request: Request) -> JSONResponse:
    """Unregister a repo via POST with {"path": "/abs/path/to/repo"}.

    Removes from catalog DB and stops the live daemon slot (if active).
    """
    body = await request.json()
    path = Path(body.get("path", ""))
    if not path.is_absolute():
        return JSONResponse({"error": f"Invalid path: {path}"}, status_code=400)

    # Resolve repo name BEFORE removing from catalog
    repo_name = daemon.registry.get_repo_name_for_path(path)

    removed = daemon.registry.unregister(path)
    if not removed:
        return JSONResponse({"error": "Path not registered"}, status_code=404)

    deactivated = False
    if repo_name:
        deactivated = await daemon.deactivate_repo(repo_name)

    return JSONResponse({"removed": True, "deactivated": deactivated})

async def _repo_health(daemon: GlobalDaemon, request: Request) -> JSONResponse:
    """Per-repo health check."""
    name = request.path_params["name"]
    slot = daemon._slots.get(name)
    if slot is None:
        return JSONResponse({"error": f"Repo '{name}' not active"}, status_code=404)
    return JSONResponse({
        "status": "healthy",
        "name": name,
        "worktrees": list(slot.worktrees.keys()),
    })

async def _repo_status(daemon: GlobalDaemon, request: Request) -> JSONResponse:
    """Per-repo detailed status."""
    name = request.path_params["name"]
    slot = daemon._slots.get(name)
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

async def _repo_reindex(daemon: GlobalDaemon, request: Request) -> Response:
    """Per-repo reindex trigger."""
    name = request.path_params["name"]
    slot = daemon._slots.get(name)
    if slot is None:
        return JSONResponse({"error": f"Repo '{name}' not active"}, status_code=404)
    for wt_name, wt_slot in slot.worktrees.items():
        slot.indexer.queue_paths(wt_name, [wt_slot.repo_root])
    return Response(status_code=202)

async def _repo_refresh_worktrees(
    daemon: GlobalDaemon, dev_mode: bool, request: Request,
) -> JSONResponse:
    """Discover and activate new git worktrees for a repo at runtime."""
    name = request.path_params["name"]
    if name not in daemon._slots:
        return JSONResponse({"error": f"Repo '{name}' not active"}, status_code=404)
    new_wts = await daemon.refresh_worktrees(name, dev_mode=dev_mode)
    return JSONResponse({"added_worktrees": new_wts})

# App builder

def build_global_app(daemon: GlobalDaemon, *, dev_mode: bool = False) -> Starlette:
    """Build the global Starlette application with all routes.

    Per-repo and per-worktree routes are handled dynamically so that repos
    registered after startup are reachable immediately — the Starlette app
    does not need to be rebuilt.
    """
    routes = [
        Route("/health", partial(_health, daemon), methods=["GET"]),
        Route("/catalog", partial(_catalog_list, daemon), methods=["GET"]),
        Route("/catalog/register", partial(_catalog_register, daemon, dev_mode), methods=["POST"]),
        Route("/catalog/unregister", partial(_catalog_unregister, daemon), methods=["POST"]),
        Route("/repos/{name}/health", partial(_repo_health, daemon), methods=["GET"]),
        Route("/repos/{name}/status", partial(_repo_status, daemon), methods=["GET"]),
        Route("/repos/{name}/reindex", partial(_repo_reindex, daemon), methods=["POST"]),
        Route("/repos/{name}/refresh-worktrees", partial(_repo_refresh_worktrees, daemon, dev_mode), methods=["POST"]),
        # Catch-all for /repos/{name}/worktrees/{wt}/mcp/...
        DynamicMcpRouter(daemon),
    ]

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        # MCP sub-app lifespans are already running (started in _activate_worktree).
        yield
        await daemon.stop_all()

    return Starlette(routes=routes, lifespan=lifespan)

# Dynamic MCP dispatcher

async def _asgi_not_found(scope: Scope, receive: Receive, send: Send) -> None:  # noqa: ARG001
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

class DynamicMcpRouter:
    """Starlette BaseRoute that dispatches /repos/{name}/worktrees/{wt}/mcp/... requests.

    Implements the three-method Starlette BaseRoute protocol (matches / handle /
    url_path_for) so it can be dropped directly into the Starlette route list.
    Looks up the live WorktreeSlot at request time, so repos and worktrees
    activated after build_global_app() are reachable immediately.
    """

    def __init__(self, daemon: GlobalDaemon) -> None:
        self._daemon = daemon

    # Starlette BaseRoute protocol

    def matches(self, scope: Scope) -> tuple[Any, Any]:
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

    def url_path_for(self, name: str, /, **path_params: Any) -> NoReturn:
        from starlette.routing import NoMatchFound

        raise NoMatchFound(name, path_params)

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        from coderecon.daemon.resolve import resolve_worktree

        path: str = scope.get("path", "")
        parts = path.lstrip("/").split("/", 4)
        repo_name = parts[1]
        wt_name = parts[3]

        wt_slot = await resolve_worktree(self._daemon, repo_name, wt_name)
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
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        from starlette.routing import Match

        match, _ = self.matches(scope)
        if match == Match.NONE:
            await _asgi_not_found(scope, receive, send)
            return
        await self.handle(scope, receive, send)
