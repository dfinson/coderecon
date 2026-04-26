"""Dev/training core functions — index introspection helpers.

These are internal tools used for development and training, not part of
the public product surface.  They are registered in the dispatch table
but don't go through MCP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from coderecon.mcp.context import AppContext


async def dev_index_facts(app_ctx: "AppContext", **kwargs: Any) -> dict[str, Any]:
    """Return structured index metadata for LLM grounding."""
    from sqlmodel import col, select

    from coderecon.index._internal.db.models import DefFact, File, ImportFact

    db = app_ctx.coordinator.db
    worktree_id: int | None = None
    if kwargs.get("worktree"):
        worktree_id = app_ctx.coordinator._get_or_create_worktree_id(kwargs["worktree"])

    with db.session() as session:
        # File query (optionally scoped to worktree)
        file_q = select(File)
        if worktree_id is not None:
            file_q = file_q.where(File.worktree_id == worktree_id)
        files = list(session.exec(file_q).all())

        # Top-level dirs
        top_dirs = sorted({
            p.path.split("/")[0]
            for p in files
            if "/" in p.path
        })

        # Languages
        lang_counts: dict[str, int] = {}
        for f in files:
            lang = f.language_family or "unknown"
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
        languages = [
            {"language": lang, "count": count}
            for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1])
        ]

        # Def counts + samples
        file_ids = [f.id for f in files if f.id is not None]
        if file_ids:
            defs = list(session.exec(
                select(DefFact).where(col(DefFact.file_id).in_(file_ids))
            ).all())
        else:
            defs = []

        classes = sorted({d.name for d in defs if d.kind == "class"})[:50]
        functions = sorted({d.name for d in defs if d.kind in ("function", "method")})[:50]

        # External deps
        if file_ids:
            imports = list(session.exec(
                select(ImportFact.source_literal).where(
                    col(ImportFact.file_id).in_(file_ids),
                    (ImportFact.resolved_path == None) | (ImportFact.resolved_path == ""),  # noqa: E711
                )
            ).all())
            dep_counts: dict[str, int] = {}
            for src in imports:
                if src:
                    dep_counts[src] = dep_counts.get(src, 0) + 1
            external_deps = [
                k for k, _ in sorted(dep_counts.items(), key=lambda x: -x[1])
            ][:30]
        else:
            external_deps = []

    return {
        "top_dirs": top_dirs,
        "languages": languages,
        "classes": classes,
        "functions": functions,
        "external_deps": external_deps,
        "file_count": len(files),
        "def_count": len(defs),
    }


async def dev_lookup_defs(app_ctx: "AppContext", **kwargs: Any) -> dict[str, Any]:
    """Look up definitions by coordinates."""
    from sqlmodel import col, select

    from coderecon.index._internal.db.models import DefFact, File

    db = app_ctx.coordinator.db
    worktree_id: int | None = None
    if kwargs.get("worktree"):
        worktree_id = app_ctx.coordinator._get_or_create_worktree_id(kwargs["worktree"])

    with db.session() as session:
        # Build file_id filter
        file_q = select(File.id, File.path, File.language_family)
        if worktree_id is not None:
            file_q = file_q.where(File.worktree_id == worktree_id)
        if kwargs.get("path"):
            file_q = file_q.where(File.path == kwargs["path"])
        file_rows = list(session.exec(file_q).all())
        file_map = {fid: (fpath, flang) for fid, fpath, flang in file_rows}

        if not file_map:
            return {"defs": []}

        # Query defs
        def_q = select(DefFact).where(col(DefFact.file_id).in_(list(file_map.keys())))
        if kwargs.get("name"):
            def_q = def_q.where(DefFact.name == kwargs["name"])
        if kwargs.get("kind"):
            def_q = def_q.where(DefFact.kind == kwargs["kind"])
        if kwargs.get("start_line") is not None:
            # Fuzzy match: within ±5 lines
            target = kwargs["start_line"]
            def_q = def_q.where(
                DefFact.start_line >= target - 5,
                DefFact.start_line <= target + 5,
            )

        defs = list(session.exec(def_q).all())

        # Optionally filter by end_line range
        if kwargs.get("end_line") is not None:
            target_end = kwargs["end_line"]
            defs = [d for d in defs if d.end_line is not None and abs(d.end_line - target_end) <= 5]

        entries = []
        for d in defs:
            fpath, flang = file_map.get(d.file_id, ("", ""))
            entries.append({
                "path": fpath,
                "name": d.name,
                "kind": d.kind,
                "start_line": d.start_line,
                "end_line": d.end_line or 0,
                "uid": d.uid or "",
                "language_family": flang,
                "qualified_name": d.qualified_name or "",
                "lexical_path": d.lexical_path or "",
                "has_docstring": bool(d.has_docstring),
                "has_decorators": bool(d.has_decorators),
                "has_return_type": bool(d.has_return_type),
                "object_size_lines": (d.end_line or d.start_line) - d.start_line,
            })

    return {"defs": entries}


async def dev_index_status(app_ctx: "AppContext", **kwargs: Any) -> dict[str, Any]:
    """Return file/def counts for a worktree."""
    from sqlmodel import col, func, select

    from coderecon.index._internal.db.models import DefFact, File

    db = app_ctx.coordinator.db
    wt_name = kwargs.get("worktree") or "main"
    worktree_id: int | None = None
    if kwargs.get("worktree"):
        worktree_id = app_ctx.coordinator._get_or_create_worktree_id(kwargs["worktree"])

    with db.session() as session:
        file_q = select(func.count()).select_from(File)
        if worktree_id is not None:
            file_q = file_q.where(File.worktree_id == worktree_id)
        file_count = session.exec(file_q).one()

        # Count defs for these files
        if worktree_id is not None:
            fids_q = select(File.id).where(File.worktree_id == worktree_id)
            def_count = session.exec(
                select(func.count()).select_from(DefFact).where(
                    col(DefFact.file_id).in_(fids_q)
                )
            ).one()
        else:
            def_count = session.exec(
                select(func.count()).select_from(DefFact)
            ).one()

    return {
        "worktree": wt_name,
        "file_count": file_count,
        "def_count": def_count,
        "initialized": app_ctx.coordinator._initialized,
    }
