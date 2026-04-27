"""Harvester E: Graph walk — structural adjacency from top candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from coderecon.mcp.tools.recon.merge import _select_graph_seeds
from coderecon.mcp.tools.recon.models import (
    EvidenceRecord,
    HarvestCandidate,
)

if TYPE_CHECKING:
    from coderecon.mcp.context import AppContext
    from coderecon.mcp.tools.recon.models import ParsedTask

log = structlog.get_logger(__name__)

async def _harvest_graph(
    app_ctx: AppContext,
    merged: dict[str, HarvestCandidate],
    parsed: ParsedTask,
) -> dict[str, HarvestCandidate]:
    """Harvester E: Walk 1-hop graph edges from top merged candidates.
    Takes seeds (candidates found by ≥2 retrievers or explicitly mentioned),
    discovers structurally adjacent defs via callees, callers, and siblings.
    Emits raw edge type and seed rank per candidate. No quality scoring,
    no budget cap — the ranker learns relevance from data.
    """
    from coderecon.index._internal.indexing.graph import FactQueries
    coordinator = app_ctx.coordinator
    candidates: dict[str, HarvestCandidate] = {}
    if not merged:
        return candidates
    seed_uids = _select_graph_seeds(merged)
    if not seed_uids:
        return candidates
    # Resolve DefFacts for seeds — batch lookup for missing ones
    seeds_with_facts: list[tuple[str, HarvestCandidate]] = []
    missing_uids = [uid for uid in seed_uids if merged[uid].def_fact is None]
    if missing_uids:
        defs_map = coordinator.batch_get_defs(missing_uids)
        for uid, d in defs_map.items():
            merged[uid].def_fact = d
    for uid in seed_uids:
        cand = merged[uid]
        if cand.def_fact is not None:
            seeds_with_facts.append((uid, cand))
    if not seeds_with_facts:
        return candidates
    # Collect all edges: (def_uid, def_fact, edge_type, seed_rank, detail, ref_tier)
    EdgeInfo = tuple[str, object, str, int, str, str | None]
    raw_edges: list[EdgeInfo] = []
    with coordinator.db.session() as session:
        fq = FactQueries(session)
        # Pre-resolve all seed file IDs to File objects in one batch
        seed_file_ids = list({sc.def_fact.file_id for _, sc in seeds_with_facts if sc.def_fact})
        files_by_id = fq.batch_get_files(seed_file_ids)
        for seed_idx, (seed_uid, seed_cand) in enumerate(seeds_with_facts, 1):
            seed_def = seed_cand.def_fact
            assert seed_def is not None
            # (a) Callees
            callees = fq.list_callees_in_scope(
                seed_def.file_id,
                seed_def.start_line,
                seed_def.end_line,
            )
            for callee in callees:
                if callee.def_uid == seed_uid:
                    continue
                raw_edges.append((
                    callee.def_uid, callee, "callee", seed_idx,
                    f"callee of {seed_def.name}", None,
                ))
            # (b) Callers — track ref_tier for quality signal
            refs = fq.list_refs_by_def_uid(seed_uid)
            caller_file_ids: set[int] = set()
            for ref in refs:
                if ref.file_id == seed_def.file_id:
                    continue
                if ref.file_id in caller_file_ids:
                    continue
                caller_file_ids.add(ref.file_id)
                caller_defs = fq.list_defs_in_file(ref.file_id)
                for cd in caller_defs:
                    if (
                        ref.start_line is not None
                        and cd.start_line <= ref.start_line <= cd.end_line
                    ):
                        raw_edges.append((
                            cd.def_uid, cd, "caller", seed_idx,
                            f"caller of {seed_def.name}",
                            ref.ref_tier,
                        ))
                        break
            # (c) Same-file siblings
            frec = files_by_id.get(seed_def.file_id)
            if frec is not None and frec.id is not None:
                sibling_defs = fq.list_defs_in_file(frec.id)
                for sd in sibling_defs:
                    if sd.def_uid == seed_uid:
                        continue
                    if sd.kind in CONSTANT_KINDS:
                        continue
                    raw_edges.append((
                        sd.def_uid, sd, "sibling", seed_idx,
                        f"sibling of {seed_def.name} in {frec.path}", None,
                    ))
            # (d) Type hierarchy — co-implementors of same interface
            if seed_def.kind in TYPE_KINDS:
                co_impl_uids = fq.list_co_implementors(seed_uid)
                if co_impl_uids:
                    co_defs = fq.batch_get_defs(co_impl_uids)
                    for co_uid, co_def in co_defs.items():
                        raw_edges.append((
                            co_uid, co_def, "implementor", seed_idx,
                            f"co-implements interface with {seed_def.name}",
                            None,
                        ))
            # (e) DocCrossRef — defs mentioned in this def's docstring
            doc_xrefs = fq.list_doc_xrefs_from(seed_uid)
            for xref in doc_xrefs:
                target_def = fq.get_def(xref.target_def_uid)
                if target_def is not None:
                    raw_edges.append((
                        xref.target_def_uid, target_def, "doc_xref", seed_idx,
                        f"referenced in docstring of {seed_def.name}", None,
                    ))
    # Deduplicate: per uid, keep lowest seed_rank (closest to top seed)
    best_edges: dict[str, EdgeInfo] = {}
    for edge in raw_edges:
        uid = edge[0]
        if uid not in best_edges or edge[3] < best_edges[uid][3]:
            best_edges[uid] = edge
    # Track best ref_tier per uid across all caller edges
    candidates = _build_graph_candidates(raw_edges, best_edges, merged)
    log.debug(
        "recon.harvest.graph",
        count=len(candidates),
        seeds_used=len(seeds_with_facts),
    )
    return candidates


def _build_graph_candidates(
    raw_edges: list[tuple[str, object, str, int, str, str | None]],
    best_edges: dict[str, tuple[str, object, str, int, str, str | None]],
    merged: dict[str, HarvestCandidate],
) -> dict[str, HarvestCandidate]:
    """Deduplicate edges and build HarvestCandidate dicts."""
    _TIER_ORDER = {"proven": 0, "strong": 1, "anchored": 2, "unknown": 3}
    best_ref_tier: dict[str, str | None] = {}
    for edge in raw_edges:
        uid, _, etype, _, _, rtier = edge
        if etype == "caller" and rtier:
            prev = best_ref_tier.get(uid)
            if prev is None or _TIER_ORDER.get(rtier, 99) < _TIER_ORDER.get(prev, 99):
                best_ref_tier[uid] = rtier

    candidates: dict[str, HarvestCandidate] = {}
    for uid, (_, def_fact, edge_type, seed_rank, detail, _ref_tier) in best_edges.items():
        caller_tier = best_ref_tier.get(uid)
        if uid in merged:
            existing = merged[uid]
            existing.from_graph = True
            if existing.graph_edge_type is None:
                existing.graph_edge_type = edge_type
                existing.graph_seed_rank = seed_rank
            if caller_tier and (
                existing.graph_caller_max_tier is None
                or _TIER_ORDER.get(caller_tier, 99)
                < _TIER_ORDER.get(existing.graph_caller_max_tier, 99)
            ):
                existing.graph_caller_max_tier = caller_tier
            if not any(e.category == "graph" for e in existing.evidence):
                existing.evidence.append(
                    EvidenceRecord(category="graph", detail=detail, score=1.0)
                )
            continue
        if uid in candidates:
            candidates[uid].graph_edge_type = edge_type
            candidates[uid].graph_seed_rank = seed_rank
            if caller_tier:
                candidates[uid].graph_caller_max_tier = caller_tier
            continue
        candidates[uid] = HarvestCandidate(
            def_uid=uid,
            def_fact=def_fact,  # type: ignore[arg-type]
            from_graph=True,
            graph_edge_type=edge_type,
            graph_seed_rank=seed_rank,
            graph_caller_max_tier=caller_tier,
            evidence=[EvidenceRecord(category="graph", detail=detail, score=1.0)],
        )
    return candidates
