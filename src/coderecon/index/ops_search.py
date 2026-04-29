"""Search operations for the index coordinator.

Standalone functions extracted from IndexCoordinatorEngine. Each takes
``engine`` as its first parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import case, func
from sqlmodel import col, select

from coderecon.index.models import DefFact, File
from coderecon.index.ops_glob import _matches_filter_paths
from coderecon.index.ops_types import SearchMode, SearchResponse, SearchResult

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine


def score_files_bm25(
    engine: IndexCoordinatorEngine, query: str, limit: int = 500
) -> dict[str, float]:
    """Score files by BM25 relevance to *query* using Tantivy."""
    if engine._lexical is None:
        return {}
    return engine._lexical.score_files_bm25(
        query, limit=limit, worktrees=engine._search_worktrees
    )


async def search(
    engine: IndexCoordinatorEngine,
    query: str,
    mode: str = SearchMode.TEXT,
    limit: int = 100,
    offset: int = 0,
    context_lines: int = 1,
    filter_languages: list[str] | None = None,
    filter_paths: list[str] | None = None,
) -> SearchResponse:
    """Search the index. Thread-safe, no locks needed."""
    await engine.wait_for_freshness()
    if engine._lexical is None:
        return SearchResponse(results=[])
    # If filtering by languages, pre-compute the set of allowed paths
    allowed_paths: set[str] | None = None
    if filter_languages:
        with engine.db.session() as session:
            stmt = select(File.path).where(col(File.language_family).in_(filter_languages))
            allowed_paths = set(session.exec(stmt).all())
            # If no files match the language filter, return empty results early
            if not allowed_paths:
                return SearchResponse(results=[])
    # Request more results than limit if filtering, to account for filtering
    # Also account for offset to support pagination
    base_limit = offset + limit
    has_filters = filter_languages or filter_paths
    search_limit = base_limit * 3 if has_filters else base_limit
    # Use appropriate search method based on mode
    if mode == SearchMode.SYMBOL:
        # Delegate to search_symbols() which uses SQLite + Tantivy fallback.
        # Combine filter_languages (resolved to allowed_paths) with user filter_paths
        symbol_filter_paths = filter_paths
        if allowed_paths is not None:
            if symbol_filter_paths:
                # Both filters present: combine them
                symbol_filter_paths = list(allowed_paths) + symbol_filter_paths
            else:
                symbol_filter_paths = list(allowed_paths)
        return await search_symbols(
            engine,
            query,
            filter_paths=symbol_filter_paths,
            limit=limit,
            offset=offset,
        )
    elif mode == SearchMode.PATH:
        search_results = engine._lexical.search_path(
            query, limit=search_limit, context_lines=context_lines,
            worktrees=engine._search_worktrees,
        )
    else:
        search_results = engine._lexical.search(
            query, limit=search_limit, context_lines=context_lines,
            worktrees=engine._search_worktrees,
        )
    # Filter results by language if requested
    filtered_hits = search_results.results
    if allowed_paths is not None:
        filtered_hits = [hit for hit in filtered_hits if hit.file_path in allowed_paths]
    # Filter results by path patterns if requested
    if filter_paths:
        filtered_hits = [
            hit for hit in filtered_hits if _matches_filter_paths(hit.file_path, filter_paths)
        ]
    # Apply offset and limit after filtering
    results = [
        SearchResult(
            path=hit.file_path,
            line=hit.line,
            column=hit.column,
            snippet=hit.snippet,
            score=hit.score,
        )
        for hit in filtered_hits[offset : offset + limit]
    ]
    return SearchResponse(
        results=results,
        fallback_reason=search_results.fallback_reason,
    )


async def search_symbols(
    engine: IndexCoordinatorEngine,
    query: str,
    *,
    filter_kinds: list[str] | None = None,
    filter_paths: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> SearchResponse:
    """Search symbols by substring match. Thread-safe.
    Uses SQLite (DefFact table) as primary source for substring + kind
    filtering, with Tantivy fallback for symbols not in the structural
    index (unsupported languages, parse failures, timing gaps).
    """
    await engine.wait_for_freshness()
    # Phase 1: SQLite structured search (substring + kind filtering)
    results: list[SearchResult] = []
    seen: set[tuple[str, int, int]] = set()  # (path, line, col) dedup key
    query_lower = query.lower()
    with engine.db.session() as session:
        # Compute match quality in SQL so ORDER BY is deterministic
        match_score = case(
            (func.lower(DefFact.name) == query_lower, 1.0),
            (func.lower(DefFact.name).startswith(query_lower), 0.8),
            else_=0.6,
        ).label("match_score")
        stmt = (
            select(DefFact, File.path, match_score)
            .join(
                File,
                DefFact.file_id == File.id,  # type: ignore[arg-type]
            )
            .where(func.lower(DefFact.name).contains(query_lower))
        )
        if filter_kinds:
            stmt = stmt.where(col(DefFact.kind).in_(filter_kinds))
        stmt = stmt.order_by(
            match_score.desc(),
            DefFact.name,
            File.path,
            col(DefFact.start_line),
            col(DefFact.start_col),
        )
        # Over-fetch to account for offset + path filtering
        stmt = stmt.limit((offset + limit) * 2)
        rows = session.exec(stmt).all()
    skipped = 0
    for def_fact, file_path, score in rows:
        # Apply path filter if requested
        if filter_paths and not _matches_filter_paths(file_path, filter_paths):
            continue
        key = (file_path, def_fact.start_line, def_fact.start_col)
        if key not in seen:
            seen.add(key)
            # Skip offset results before collecting
            if skipped < offset:
                skipped += 1
                continue
            results.append(
                SearchResult(
                    path=file_path,
                    line=def_fact.start_line,
                    column=def_fact.start_col,
                    snippet=def_fact.display_name or def_fact.name,
                    score=float(score),
                )
            )
        if len(results) >= limit:
            break
    # Phase 2: Tantivy fallback (only if Phase 1 didn't fill limit)
    # Skip fallback when filter_kinds is set — Tantivy has no kind metadata
    if len(results) < limit and engine._lexical is not None and not filter_kinds:
        tantivy_results = engine._lexical.search_symbols(
            query, limit=limit, context_lines=1, worktrees=engine._search_worktrees
        )
        # Cap fallback scores below the lowest Phase 1 score
        phase1_min = min((r.score for r in results), default=0.5)
        for hit in tantivy_results.results:
            key = (hit.file_path, hit.line, hit.column)
            if key in seen:
                continue
            # Apply path filter if requested
            if filter_paths and not _matches_filter_paths(hit.file_path, filter_paths):
                continue
            seen.add(key)
            results.append(
                SearchResult(
                    path=hit.file_path,
                    line=hit.line,
                    column=hit.column,
                    snippet=hit.snippet,
                    score=phase1_min - 0.01,
                )
            )
            if len(results) >= limit:
                break
    # Sort by score descending
    results.sort(key=lambda r: -r.score)
    return SearchResponse(results=results[:limit])
