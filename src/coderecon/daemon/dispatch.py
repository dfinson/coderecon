"""Stdio dispatch — routes method names to core functions.

The ``CORE_FUNCTIONS`` table maps method strings directly to the same
extracted core functions that the MCP ``@mcp.tool`` wrappers call.
There is **no** parallel handler layer.

Management methods (``register``, ``catalog``, ``status``,
``session_close``) are handled inline because they don't need a
repo/worktree context.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

import structlog

from coderecon.daemon.resolve import resolve_worktree

if TYPE_CHECKING:
    from coderecon.catalog.registry import CatalogRegistry
    from coderecon.daemon.event_bus import EventBus
    from coderecon.daemon.global_app import GlobalDaemon

log = structlog.get_logger(__name__)

# Type for core functions: (app_ctx, session?, **params) -> dict (sync or async)
CoreFn = Callable[..., Any]

# Lazy-loaded dispatch table — avoids importing every tool module at startup

_CORE_FUNCTIONS: dict[str, CoreFn] | None = None

def _load_core_functions() -> dict[str, CoreFn]:
    """Build the dispatch table on first use."""
    from coderecon.daemon.dev_tools import (
        dev_index_facts,
        dev_index_status,
        dev_lookup_defs,
    )
    from coderecon.mcp.tools.checkpoint_pipeline import checkpoint_pipeline
    from coderecon.mcp.tools.diff import semantic_diff_core
    from coderecon.mcp.tools.graph import (
        graph_communities_core,
        graph_cycles_core,
        graph_export_core,
        recon_understand_core,
    )
    from coderecon.mcp.tools.introspection import describe_core
    from coderecon.mcp.tools.recon import (
        raw_signals_pipeline,
        recon_map_core,
        recon_pipeline,
    )
    from coderecon.mcp.tools.refactor import (
        recon_impact_core,
        refactor_cancel_core,
        refactor_commit_core,
        refactor_move_core,
        refactor_rename_core,
    )

    return {
        "recon": recon_pipeline,
        "recon_map": recon_map_core,
        "raw_signals": raw_signals_pipeline,
        "recon_impact": recon_impact_core,
        "recon_understand": recon_understand_core,
        "semantic_diff": semantic_diff_core,
        "graph_cycles": graph_cycles_core,
        "graph_communities": graph_communities_core,
        "graph_export": graph_export_core,
        "refactor_rename": refactor_rename_core,
        "refactor_move": refactor_move_core,
        "refactor_commit": refactor_commit_core,
        "refactor_cancel": refactor_cancel_core,
        "checkpoint": checkpoint_pipeline,
        "describe": describe_core,
        # Dev/training methods — not part of the public product surface
        "index_facts": dev_index_facts,
        "lookup_defs": dev_lookup_defs,
        "index_status": dev_index_status,
    }

def _get_core_functions() -> dict[str, CoreFn]:
    global _CORE_FUNCTIONS  # noqa: PLW0603
    if _CORE_FUNCTIONS is None:
        _CORE_FUNCTIONS = _load_core_functions()
    return _CORE_FUNCTIONS

# Management methods — no repo context needed

# Methods that the dispatch loop handles without resolving a repo/worktree.
MANAGEMENT_METHODS = frozenset({
    "register", "unregister", "catalog", "status", "session_close",
    "describe", "reindex",
})

# Methods whose core functions accept (app_ctx, **params) — no session arg.
_NO_SESSION_METHODS = frozenset({
    "recon_map", "graph_cycles", "graph_communities", "graph_export",
    "recon_understand",
    # Dev/training methods
    "index_facts", "lookup_defs", "index_status",
})

async def _handle_register(
    daemon: GlobalDaemon,
    registry: CatalogRegistry,
    params: dict[str, Any],
    request_id: str | None,
) -> dict[str, Any]:
    repo_path = params.get("path")
    if not repo_path:
        return _error_response(request_id, "INVALID_PARAMS", "register requires 'path'")
    try:
        repo, wt = registry.register(Path(repo_path))
        return _success_response(request_id, {
            "repo": repo.name,
            "worktree": wt.name,
        })
    except (OSError, ValueError, RuntimeError) as exc:
        return _error_response(request_id, "REGISTER_FAILED", str(exc))

async def _handle_catalog(
    registry: CatalogRegistry,
    request_id: str | None,
) -> dict[str, Any]:
    repos = registry.list_repos()
    entries = []
    for r in repos:
        wts = registry.list_worktrees(r.name)
        entries.append({
            "name": r.name,
            "git_dir": r.git_dir,
            "worktrees": [{"name": w.name, "root_path": w.root_path} for w in wts],
        })
    return _success_response(request_id, {"repos": entries})

async def _handle_status(
    daemon: GlobalDaemon,
    request_id: str | None,
) -> dict[str, Any]:
    slots = []
    for name in daemon.slot_names:
        slot = daemon.get_slot(name)
        if slot is None:
            continue
        slots.append({
            "name": name,
            "worktrees": list(slot.worktrees.keys()),
        })
    return _success_response(request_id, {
        "daemon_healthy": True,
        "active_repos": slots,
    })

async def _handle_describe(
    params: dict[str, Any],
    request_id: str | None,
) -> dict[str, Any]:
    core_fns = _get_core_functions()
    describe_fn = core_fns["describe"]
    # Supply available tool names from the dispatch table so describe can
    # report what tools exist even when static docs are missing.
    params.setdefault("available_tool_names", list(core_fns.keys()))
    result = describe_fn(**params)
    return _success_response(request_id, result)

async def _handle_unregister(
    registry: CatalogRegistry,
    params: dict[str, Any],
    request_id: str | None,
) -> dict[str, Any]:
    repo_path = params.get("path")
    if not repo_path:
        return _error_response(request_id, "INVALID_PARAMS", "unregister requires 'path'")
    try:
        ok = registry.unregister(Path(repo_path))
        return _success_response(request_id, {"removed": ok})
    except (OSError, ValueError, RuntimeError) as exc:
        return _error_response(request_id, "UNREGISTER_FAILED", str(exc))

async def _handle_reindex(
    daemon: GlobalDaemon,
    params: dict[str, Any],
    request_id: str | None,
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    repo_name = params.get("repo")
    if not repo_name:
        return _error_response(request_id, "INVALID_PARAMS", "reindex requires 'repo'")
    worktree = params.get("worktree")
    wt_slot = await resolve_worktree(daemon, repo_name, worktree)
    if wt_slot is None:
        return _error_response(
            request_id, "NOT_FOUND",
            f"No repo '{repo_name}' / worktree '{worktree or 'default'}'",
        )
    coordinator = wt_slot.app_ctx.coordinator

    # Progress callback → emits index.progress events over the wire.
    # Uses emit_sync because initialize() blocks the event loop —
    # async emit would never flush until the blocking code finishes.
    def _on_progress(indexed: int, total: int, by_ext: dict, phase: str) -> None:
        if event_bus is not None:
            event_bus.emit_sync("index.progress", {
                "repo": repo_name,
                "indexed": indexed,
                "total": total,
                "phase": phase,
            })

    if not coordinator._initialized:
        result = await coordinator.initialize(_on_progress)
        return _success_response(request_id, {
            "reindexed": True,
            "files_indexed": result.files_indexed,
        })
    stats = await coordinator.reindex_full()
    return _success_response(request_id, {
        "reindexed": True,
        "files_indexed": getattr(stats, "files_indexed", 0),
    })

async def _handle_session_close(
    daemon: GlobalDaemon,
    params: dict[str, Any],
    request_id: str | None,
) -> dict[str, Any]:
    session_id = params.get("session_id")
    repo_name = params.get("repo")
    worktree = params.get("worktree")
    if not session_id or not repo_name:
        return _error_response(
            request_id, "INVALID_PARAMS",
            "session_close requires 'session_id' and 'repo'",
        )
    wt_slot = await resolve_worktree(daemon, repo_name, worktree)
    if wt_slot is None:
        return _error_response(request_id, "NOT_FOUND", f"No repo '{repo_name}'")
    wt_slot.app_ctx.session_manager.close(session_id)
    return _success_response(request_id, {"closed": True})

# Main dispatch

async def dispatch(
    daemon: GlobalDaemon,
    registry: CatalogRegistry,
    request: dict[str, Any],
    *,
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Route a single stdio request to the appropriate handler.

    Returns a response dict (with ``id``, ``result`` or ``error``).
    """
    method: str | None = request.get("method")
    params: dict[str, Any] = dict(request.get("params", {}))
    request_id: str | None = request.get("id")
    session_id: str | None = request.get("session_id")

    if not method:
        return _error_response(request_id, "INVALID_REQUEST", "Missing 'method'")

    try:
        # ── Management methods ──
        if method == "register":
            return await _handle_register(daemon, registry, params, request_id)
        if method == "catalog":
            return await _handle_catalog(registry, request_id)
        if method == "status":
            return await _handle_status(daemon, request_id)
        if method == "session_close":
            return await _handle_session_close(daemon, params, request_id)
        if method == "describe":
            return await _handle_describe(params, request_id)
        if method == "unregister":
            return await _handle_unregister(registry, params, request_id)
        if method == "reindex":
            return await _handle_reindex(daemon, params, request_id, event_bus)

        # ── Tool methods — resolve repo + worktree ──
        repo_name = params.pop("repo", None)
        if not repo_name:
            return _error_response(
                request_id, "INVALID_PARAMS",
                f"Method '{method}' requires 'repo' in params",
            )
        worktree = params.pop("worktree", None)

        wt_slot = await resolve_worktree(daemon, repo_name, worktree)
        if wt_slot is None:
            return _error_response(
                request_id, "NOT_FOUND",
                f"No repo '{repo_name}' / worktree '{worktree or 'default'}'",
            )

        app_ctx = wt_slot.app_ctx
        core_fns = _get_core_functions()
        core_fn = core_fns.get(method)
        if core_fn is None:
            return _error_response(
                request_id, "METHOD_NOT_FOUND",
                f"Unknown method '{method}'",
            )

        # Invoke core function — some take session, some don't
        if method in _NO_SESSION_METHODS:
            result = core_fn(app_ctx, **params)
        else:
            if not session_id:
                session_id = f"stdio_{repo_name}_{worktree or 'main'}"
            session = app_ctx.session_manager.get_or_create(session_id)
            result = core_fn(app_ctx, session, **params)
        if inspect.isawaitable(result):
            result = await result

        return _success_response(request_id, result)

    except KeyError as exc:
        return _error_response(request_id, "INVALID_PARAMS", f"Missing param: {exc}")
    except Exception as exc:
        log.error("dispatch.error", method=method, exc_info=True)
        return _error_response(request_id, "INTERNAL", str(exc))

# Response helpers

def _success_response(request_id: str | None, result: Any) -> dict[str, Any]:
    resp: dict[str, Any] = {"result": result}
    if request_id is not None:
        resp["id"] = request_id
    return resp

def _error_response(request_id: str | None, code: str, message: str) -> dict[str, Any]:
    resp: dict[str, Any] = {"error": {"code": code, "message": message}}
    if request_id is not None:
        resp["id"] = request_id
    return resp
