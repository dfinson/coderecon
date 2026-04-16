"""Reciprocal Rank Fusion — model-free ranking fallback.

Fuses 6 harvester rank lists (term-match, explicit, graph, import,
shares-file-with-seed, coverage-linked) into a single score per candidate
using the standard RRF formula:

    score(d) = Σ  1 / (k + rank_i(d))

where i ranges over every list in which d appears.

Used when LightGBM models are not available.  When models *are*
available, ``rrf_fuse()`` still runs and its ``rrf_score`` is fed
to the ranker as a feature.
"""

from __future__ import annotations

from typing import Any


def rrf_fuse(candidates: list[dict[str, Any]], *, k: int = 60) -> list[dict[str, Any]]:
    """Score candidates via Reciprocal Rank Fusion across harvester lists.

    Builds four rank lists from the already-merged candidate dicts, then
    fuses them.  Returns candidates sorted descending by fused score,
    with an ``rrf_score`` key added to each dict.
    """
    uid_index: dict[str, int] = {c["def_uid"]: i for i, c in enumerate(candidates)}
    scores = [0.0] * len(candidates)

    for ranklist in _build_rank_lists(candidates):
        for rank, idx in enumerate(ranklist, start=1):
            scores[idx] += 1.0 / (k + rank)

    for i, c in enumerate(candidates):
        c["rrf_score"] = scores[i]

    return sorted(candidates, key=lambda c: -c["rrf_score"])


def rrf_file_prune(
    candidates: list[dict[str, Any]],
    *,
    max_files: int = 20,
    pinned_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Prune to top files by max RRF score, keeping pinned/seeded files.

    Returns only candidates whose file survived pruning.
    """
    pinned_paths = pinned_paths or set()

    # Aggregate: best rrf_score per file
    file_best: dict[str, float] = {}
    for c in candidates:
        path = c.get("path", "")
        file_best[path] = max(file_best.get(path, 0.0), c.get("rrf_score", 0.0))

    # Sort files by score and keep top max_files
    sorted_files = sorted(file_best.items(), key=lambda x: -x[1])
    kept = {path for path, _ in sorted_files[:max_files]}

    # Always keep pinned / agent-seeded files
    for c in candidates:
        if c.get("symbol_source") in ("pin", "agent_seed"):
            kept.add(c.get("path", ""))
    kept |= pinned_paths

    return [c for c in candidates if c.get("path", "") in kept]


# ------------------------------------------------------------------
# Internal: build per-harvester rank lists
# ------------------------------------------------------------------

def _build_rank_lists(candidates: list[dict[str, Any]]) -> list[list[int]]:
    """Return up to 5 rank lists (each a list of candidate indices, best-first)."""
    lists: list[list[int]] = []

    # 1. Term-match list: ranked by bm25_file_score desc, term_match_count desc
    term = [
        (i, c.get("bm25_file_score") or 0.0, c.get("term_match_count") or 0)
        for i, c in enumerate(candidates)
        if c.get("term_match_count")
    ]
    if term:
        term.sort(key=lambda t: (-t[1], -t[2]))
        lists.append([i for i, _, _ in term])

    # 2. Explicit list: ranked by symbol_source priority
    _src_priority = {"agent_seed": 0, "pin": 1, "path_mention": 2, "task_extracted": 3, "auto_seed": 4}
    explicit = [
        (i, _src_priority.get(c.get("symbol_source", ""), 99))
        for i, c in enumerate(candidates)
        if c.get("symbol_source") is not None
    ]
    if explicit:
        explicit.sort(key=lambda t: t[1])
        lists.append([i for i, _ in explicit])

    # 3. Graph list: ranked by caller_tier desc then seed_rank asc
    _tier_val = {"proven": 3, "strong": 2, "anchored": 1, "unknown": 0}
    graph = [
        (i, _tier_val.get(c.get("graph_caller_max_tier") or "", 0), c.get("graph_seed_rank") or 999)
        for i, c in enumerate(candidates)
        if c.get("graph_edge_type") is not None
    ]
    if graph:
        graph.sort(key=lambda t: (-t[1], t[2]))
        lists.append([i for i, _, _ in graph])

    # 4. Import list: ranked by import_direction priority
    _imp_priority = {"test_pair": 0, "reverse": 1, "forward": 2, "barrel": 3}
    imports = [
        (i, _imp_priority.get(c.get("import_direction", ""), 99))
        for i, c in enumerate(candidates)
        if c.get("import_direction") is not None
    ]
    if imports:
        imports.sort(key=lambda t: t[1])
        lists.append([i for i, _ in imports])

    # 5. Shares-file-with-seed list: short selective booster
    shares_file = [
        i for i, c in enumerate(candidates)
        if c.get("shares_file_with_seed")
    ]
    if shares_file:
        lists.append(shares_file)

    # 6. Coverage-linked list: deterministic test↔source links
    coverage = [
        i for i, c in enumerate(candidates)
        if c.get("from_coverage")
    ]
    if coverage:
        lists.append(coverage)

    return lists
