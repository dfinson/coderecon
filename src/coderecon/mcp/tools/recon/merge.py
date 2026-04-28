"""Candidate merge, enrichment, coverage expansion, and graph-seed selection.

Single Responsibility: Combine outputs from multiple harvesters and resolve
structural metadata.  No harvesting logic — just merge + enrich.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from coderecon.mcp.tools.recon.models import (
    EvidenceRecord,
    HarvestCandidate,
    _classify_artifact,
    _is_barrel_file,
    _is_test_file,
)

if TYPE_CHECKING:
    from coderecon.mcp.context import AppContext

log = structlog.get_logger(__name__)

_TIER_ORDER = {"proven": 0, "strong": 1, "anchored": 2, "unknown": 3}

def _merge_candidates(
    *harvests: dict[str, HarvestCandidate],
) -> dict[str, HarvestCandidate]:
    """Merge candidates from multiple harvesters, accumulating evidence."""
    merged: dict[str, HarvestCandidate] = {}

    for harvest in harvests:
        for uid, cand in harvest.items():
            if uid not in merged:
                merged[uid] = cand
            else:
                existing = merged[uid]
                existing.from_term_match = existing.from_term_match or cand.from_term_match
                existing.from_explicit = existing.from_explicit or cand.from_explicit
                existing.from_graph = existing.from_graph or cand.from_graph
                existing.from_coverage = existing.from_coverage or cand.from_coverage
                existing.matched_terms |= cand.matched_terms
                existing.term_match_count = max(existing.term_match_count, cand.term_match_count)
                existing.term_total_matches = max(existing.term_total_matches, cand.term_total_matches)
                existing.lex_hit_count = max(existing.lex_hit_count, cand.lex_hit_count)
                existing.bm25_file_score = max(existing.bm25_file_score, cand.bm25_file_score)
                if cand.symbol_source and not existing.symbol_source:
                    existing.symbol_source = cand.symbol_source
                if cand.graph_edge_type and not existing.graph_edge_type:
                    existing.graph_edge_type = cand.graph_edge_type
                    existing.graph_seed_rank = cand.graph_seed_rank
                if cand.graph_caller_max_tier and (
                    not existing.graph_caller_max_tier
                    or _TIER_ORDER.get(cand.graph_caller_max_tier, 99)
                    < _TIER_ORDER.get(existing.graph_caller_max_tier, 99)
                ):
                    existing.graph_caller_max_tier = cand.graph_caller_max_tier
                if cand.import_direction and not existing.import_direction:
                    existing.import_direction = cand.import_direction
                existing.splade_score = max(existing.splade_score, cand.splade_score)
                existing.evidence.extend(cand.evidence)
                if existing.def_fact is None and cand.def_fact is not None:
                    existing.def_fact = cand.def_fact

    return merged

async def _enrich_candidates(
    app_ctx: AppContext,
    candidates: dict[str, HarvestCandidate],
) -> None:
    """Resolve missing DefFact objects and populate structural metadata.

    Mutates candidates in-place.

    Performance: Uses batch file path resolution and hub score caching
    to minimize repeated queries.
    """
    from coderecon.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator

    # Resolve missing DefFacts in one batch query
    missing_uids = [uid for uid, c in candidates.items() if c.def_fact is None]
    if missing_uids:
        found = coordinator.batch_get_defs(missing_uids)
        for uid, d in found.items():
            candidates[uid].def_fact = d

    # Remove candidates that still lack a DefFact
    dead = [uid for uid, c in candidates.items() if c.def_fact is None]
    if dead:
        log.warning("enrich.dropped_candidates", count=len(dead), uids=dead[:10])
    for uid in dead:
        del candidates[uid]

    # Populate structural metadata with caching
    fid_path_cache: dict[int, str] = {}
    hub_score_cache: dict[str, int] = {}

    with coordinator.db.session() as session:
        fq = FactQueries(session)

        # Batch resolve all unique file_ids to paths
        unique_fids = list({c.def_fact.file_id for c in candidates.values() if c.def_fact})
        file_map = fq.batch_get_files(unique_fids)
        for fid, frec in file_map.items():
            fid_path_cache[fid] = frec.path if frec else ""

        # Batch resolve all hub scores
        all_uids = [uid for uid, c in candidates.items() if c.def_fact is not None]
        hub_score_cache = fq.batch_count_callers(all_uids)

        # Batch resolve endpoint status
        endpoint_cache = fq.batch_get_endpoints(all_uids)

        # Batch resolve test coverage counts
        coverage_cache = fq.batch_count_test_coverage(all_uids)

        # Build declared_module cache from file records
        module_cache: dict[int, str] = {}
        for fid, frec in file_map.items():
            module_cache[fid] = frec.declared_module or "" if frec else ""

        for uid, cand in list(candidates.items()):
            if cand.def_fact is None:
                continue
            d = cand.def_fact

            cand.hub_score = hub_score_cache.get(uid, 0)

            cand.file_path = fid_path_cache.get(d.file_id, "")
            frec = file_map.get(d.file_id)
            cand.language_family = frec.language_family or "" if frec else ""
            cand.is_test = _is_test_file(cand.file_path)
            cand.is_barrel = _is_barrel_file(cand.file_path)
            cand.is_endpoint = uid in endpoint_cache
            cand.test_coverage_count = coverage_cache.get(uid, 0)
            cand.declared_module = module_cache.get(d.file_id, "")
            cand.artifact_kind = _classify_artifact(cand.file_path)

    anchor_uids: set[str] = set()
    anchor_file_ids: set[int] = set()
    for uid, cand in candidates.items():
        if cand.def_fact is None:
            continue
        if cand.from_explicit:
            anchor_uids.add(uid)
            anchor_file_ids.add(cand.def_fact.file_id)

    if anchor_uids:
        for uid, cand in candidates.items():
            if uid in anchor_uids or cand.def_fact is None:
                continue
            if cand.def_fact.file_id in anchor_file_ids:
                cand.shares_file_with_seed = True

        with coordinator.db.session() as session:
            fq = FactQueries(session)

            anchor_callee_uids: set[str] = set()
            for anchor_uid in anchor_uids:
                anchor_cand = candidates[anchor_uid]
                if anchor_cand.def_fact is None:
                    continue
                callees = fq.list_callees_in_scope(
                    anchor_cand.def_fact.file_id,
                    anchor_cand.def_fact.start_line,
                    anchor_cand.def_fact.end_line,
                )
                for c in callees:
                    anchor_callee_uids.add(c.def_uid)

            anchor_import_uids: set[str] = set()
            seen_import_files: set[str] = set()
            for anchor_uid in anchor_uids:
                anchor_cand = candidates[anchor_uid]
                if anchor_cand.def_fact is None:
                    continue
                anchor_path = fid_path_cache.get(anchor_cand.def_fact.file_id, "")
                if not anchor_path or anchor_path in seen_import_files:
                    continue
                seen_import_files.add(anchor_path)
                imports = fq.list_imports(anchor_cand.def_fact.file_id)
                for imp in imports:
                    if imp.resolved_path:
                        imp_file = fq.get_file_by_path(imp.resolved_path)
                        if imp_file is not None and imp_file.id is not None:
                            imp_defs = fq.list_defs_in_file(imp_file.id)
                            for idef in imp_defs:
                                anchor_import_uids.add(idef.def_uid)

            for uid, cand in candidates.items():
                if uid in anchor_uids:
                    continue
                if uid in anchor_callee_uids:
                    cand.is_callee_of_top = True
                if uid in anchor_import_uids:
                    cand.is_imported_by_top = True

async def _expand_via_coverage(
    app_ctx: AppContext,
    candidates: dict[str, HarvestCandidate],
) -> dict[str, HarvestCandidate]:
    """Expand candidates via deterministic coverage links.

    Test defs already in the pool → source defs covered by those tests.

    Only uses non-stale ``TestCoverageFact`` rows — the mapping
    is deterministic from instrumented test runs.

    Returns new candidates (not yet in the pool) to be merged.
    """
    from coderecon.index._internal.indexing.graph import FactQueries

    coordinator = app_ctx.coordinator
    new: dict[str, HarvestCandidate] = {}

    test_file_paths = list({
        c.file_path for c in candidates.values()
        if c.is_test and c.file_path
    })

    if not test_file_paths:
        return new

    with coordinator.db.session() as session:
        fq = FactQueries(session)

        covered_uids = fq.batch_get_covered_def_uids(test_file_paths)
        missing = [
            uid for uid in covered_uids
            if uid not in candidates and uid not in new
        ]
        if missing:
            found = fq.batch_get_defs(missing)
            for uid, d in found.items():
                new[uid] = HarvestCandidate(
                    def_uid=uid,
                    def_fact=d,
                    from_coverage=True,
                    evidence=[
                        EvidenceRecord(
                            category="coverage",
                            detail="covered by candidate test",
                            score=1.0,
                        )
                    ],
                )

    return new

def _select_graph_seeds(
    merged: dict[str, HarvestCandidate],
    *,
    fallback_top_k: int = 10,
) -> list[str]:
    """Select candidates to use as graph seeds.

    Seeds = all candidates found by ≥2 retrievers, plus all explicit
    mentions.  When neither explicit seeds produce
    multi-evidence overlap, falls back to top-K term-match candidates
    (by matched-term count, minimum 2 terms) so the graph walk still fires.
    """
    seeds: list[str] = []
    for uid, cand in merged.items():
        if cand.from_explicit or cand.evidence_axes >= 2:
            seeds.append(uid)
    if seeds:
        return seeds

    # Fallback: best term-match candidates when no multi-evidence seeds
    term_scored = [
        (uid, len(cand.matched_terms))
        for uid, cand in merged.items()
        if cand.from_term_match and len(cand.matched_terms) >= 2
    ]
    term_scored.sort(key=lambda x: x[1], reverse=True)
    return [uid for uid, _ in term_scored[:fallback_top_k]]

def _add_file_defs_as_candidates(
    fq: object,  # FactQueries
    file_rec: object,  # File model
    candidates: dict[str, HarvestCandidate],
    merged: dict[str, HarvestCandidate],
    *,
    category: str,
    detail: str,
    score: float,
    import_direction: str | None = None,
) -> None:
    """Add defs from a file as import-discovered candidates."""
    from coderecon.index._internal.indexing.graph import FactQueries as _FQ

    fq_typed: _FQ = fq  # type: ignore[assignment]
    file_id = getattr(file_rec, "id", None)
    if file_id is None:
        return

    defs = fq_typed.list_defs_in_file(file_id)

    for d in defs:
        if d.def_uid in merged:
            existing = merged[d.def_uid]
            existing.from_graph = True
            if existing.import_direction is None:
                existing.import_direction = import_direction
            if not any(e.category == category and e.detail == detail for e in existing.evidence):
                existing.evidence.append(
                    EvidenceRecord(category=category, detail=detail, score=score)
                )
            continue
        if d.def_uid in candidates:
            if candidates[d.def_uid].import_direction is None:
                candidates[d.def_uid].import_direction = import_direction
            continue
        candidates[d.def_uid] = HarvestCandidate(
            def_uid=d.def_uid,
            def_fact=d,
            from_graph=True,
            import_direction=import_direction,
            evidence=[EvidenceRecord(category=category, detail=detail, score=score)],
        )

def _infer_test_paths(source_path: str) -> list[str]:
    """Infer candidate test file paths from a source file path.

    Handles common patterns:
    - src/foo/bar.py → tests/foo/test_bar.py
    - src/foo/bar.py → tests/test_bar.py
    - lib/foo.py → tests/test_foo.py
    """
    import os

    parts = source_path.split("/")
    basename = parts[-1]

    if basename.startswith("test_") or basename == "conftest.py":
        return []
    if not basename.endswith(".py"):
        return []

    name_stem = basename[:-3]
    test_name = f"test_{name_stem}.py"

    candidates: list[str] = []
    if len(parts) >= 2:
        sub_path = "/".join(parts[1:-1])
        if sub_path:
            candidates.append(f"tests/{sub_path}/{test_name}")
        candidates.append(f"tests/{test_name}")

    dir_path = os.path.dirname(source_path)
    if dir_path:
        candidates.append(f"{dir_path}/{test_name}")

    return candidates
