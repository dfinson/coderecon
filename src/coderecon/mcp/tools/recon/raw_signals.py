"""Raw signal endpoint for ranking model training data collection.

Returns per-DefFact candidate pools with per-retriever scores — the raw
inputs needed for the ranker/cutoff/gate training datasets.

Does NOT run any model (ranker, cutoff, gate). Does not filter, sort,
or truncate. Returns the raw union of all retriever outputs.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

from coderecon.mcp.tools.recon.harvesters import (
    _harvest_explicit,
    _harvest_graph,
    _harvest_imports,
    _harvest_splade,
    _harvest_term_match,
)
from coderecon.mcp.tools.recon.merge import (
    _enrich_candidates,
    _expand_via_coverage,
    _merge_candidates,
)
from coderecon.mcp.tools.recon.models import HarvestCandidate
from coderecon.mcp.tools.recon.parsing import parse_task

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from coderecon.mcp.context import AppContext

log = structlog.get_logger(__name__)


async def raw_signals_pipeline(
    app_ctx: AppContext,
    query: str,
    seeds: list[str] | None = None,
    pins: list[str] | None = None,
) -> dict[str, Any]:
    """Run all retrievers and return raw per-def signals without model inference.

    Returns a dict with query features, repo features, and a list of
    candidate defs with per-retriever scores and ranks.
    """
    t0 = time.monotonic()

    # Parse query
    parsed = parse_task(query)

    # Run all harvesters
    # B: Term match
    term_candidates = await _harvest_term_match(app_ctx, parsed)

    # S: SPLADE sparse retrieval
    splade_candidates = await _harvest_splade(app_ctx, parsed)

    # D: Explicit (symbols/paths from query text + agent seeds)
    explicit_candidates = await _harvest_explicit(
        app_ctx, parsed, explicit_seeds=seeds or None,
    )

    # Merge B-S-D
    merged = _merge_candidates(term_candidates, splade_candidates)
    merged = _merge_candidates(merged, explicit_candidates)

    # D2: Pin injection — add all defs from pinned files
    if pins:
        from coderecon.index._internal.indexing.graph import FactQueries
        from coderecon.mcp.tools.recon.models import EvidenceRecord

        coordinator = app_ctx.coordinator
        with coordinator.db.session() as session:
            fq = FactQueries(session)
            for pin_path in pins:
                frec = fq.get_file_by_path(pin_path)
                if frec is None or frec.id is None:
                    continue
                defs_in = fq.list_defs_in_file(frec.id)
                for d in defs_in:
                    if d.def_uid not in merged:
                        merged[d.def_uid] = HarvestCandidate(
                            def_uid=d.def_uid,
                            def_fact=d,
                            from_explicit=True,
                            symbol_source="pin",
                            evidence=[
                                EvidenceRecord(
                                    category="explicit",
                                    detail=f"pinned path '{pin_path}'",
                                    score=1.0,
                                )
                            ],
                        )
                    else:
                        existing = merged[d.def_uid]
                        existing.from_explicit = True
                        if existing.symbol_source is None:
                            existing.symbol_source = "pin"

    # E: Graph walk from top merged candidates
    graph_candidates = await _harvest_graph(app_ctx, merged, parsed)

    # Final merge with graph
    merged = _merge_candidates(merged, graph_candidates)

    # F: Import-chain discovery from top merged candidates
    import_candidates = await _harvest_imports(app_ctx, merged, parsed)
    merged = _merge_candidates(merged, import_candidates)

    # Enrich: resolve missing DefFacts, populate metadata
    await _enrich_candidates(app_ctx, merged)

    # Coverage expansion: bidirectional source↔test links
    coverage_new = await _expand_via_coverage(app_ctx, merged)
    if coverage_new:
        merged.update(coverage_new)
        await _enrich_candidates(app_ctx, coverage_new)
        # Propagate enriched data back into merged
        merged.update(coverage_new)

    # Collect seed file paths for path/package distance computation
    seed_paths: list[str] = []
    seed_modules: list[str] = []
    for _uid, cand in merged.items():
        if cand.from_explicit:
            if cand.file_path:
                seed_paths.append(cand.file_path)
            if cand.declared_module:
                seed_modules.append(cand.declared_module)

    # Build candidate list
    candidates_out: list[dict[str, Any]] = []
    for _uid, cand in merged.items():
        if cand.def_fact is None:
            continue

        d = cand.def_fact

        # Count retrievers that found this def
        retriever_hits = sum([
            cand.from_term_match,
            cand.from_graph,
            cand.from_explicit,
            cand.from_coverage,
            cand.import_direction is not None,
            cand.splade_score > 0,
        ])

        # Path tokenization
        path_parts = cand.file_path.rsplit("/", 1)
        parent_dir = path_parts[0] if len(path_parts) > 1 else ""
        path_depth = cand.file_path.count("/")

        # Nesting depth from lexical_path
        nesting_depth = d.lexical_path.count(".") if d.lexical_path else 0

        # Path distance to nearest seed (shared prefix depth)
        seed_path_distance = _min_path_distance(cand.file_path, seed_paths)

        # Package distance to nearest seed module
        same_package, package_distance = _min_package_distance(
            cand.declared_module, seed_modules
        )

        candidates_out.append({
            # Identity
            "def_uid": uid,
            "path": cand.file_path,
            "kind": d.kind,
            "name": d.name,
            "lexical_path": d.lexical_path,
            "qualified_name": d.qualified_name,
            # Span
            "start_line": d.start_line,
            "end_line": d.end_line,
            "object_size_lines": d.end_line - d.start_line + 1,
            # Path features
            "file_ext": "." + cand.file_path.rsplit(".", 1)[-1] if "." in cand.file_path else "",
            "language_family": cand.language_family,
            "parent_dir": parent_dir,
            "path_depth": path_depth,
            # Structural metadata from index
            "has_docstring": d.docstring is not None and len(d.docstring) > 0,
            "docstring": d.docstring or "",
            "has_decorators": d.decorators_json is not None and d.decorators_json != "[]",
            "has_return_type": d.return_type is not None,
            "signature_text": d.signature_text,
            "namespace": d.namespace,
            "nesting_depth": nesting_depth,
            "has_parent_scope": nesting_depth > 0,
            "hub_score": cand.hub_score,
            "is_test": cand.is_test,
            "is_barrel": cand.is_barrel,
            "is_endpoint": cand.is_endpoint,
            "test_coverage_count": cand.test_coverage_count,
            "artifact_kind": cand.artifact_kind,
            # Structural link signals
            "shares_file_with_seed": cand.shares_file_with_seed,
            "is_callee_of_top": cand.is_callee_of_top,
            "is_imported_by_top": cand.is_imported_by_top,
            # Term match signal (raw counts)
            "term_match_count": cand.term_match_count if cand.from_term_match else None,
            "term_total_matches": cand.term_total_matches if cand.from_term_match else None,
            "lex_hit_count": cand.lex_hit_count,
            "bm25_file_score": cand.bm25_file_score,
            # Graph signal (categorical)
            "graph_edge_type": cand.graph_edge_type,
            "graph_seed_rank": cand.graph_seed_rank,
            "graph_caller_max_tier": cand.graph_caller_max_tier,
            # Symbol/explicit signal (categorical)
            "symbol_source": cand.symbol_source,
            # Import signal (categorical)
            "import_direction": cand.import_direction,
            # SPLADE sparse retrieval score
            "splade_score": cand.splade_score,
            # Coverage expansion signal
            "from_coverage": cand.from_coverage,
            # Harvester source flags
            "from_term_match": cand.from_term_match,
            "from_explicit": cand.from_explicit,
            "from_graph": cand.from_graph,
            "matched_terms_count": len(cand.matched_terms),
            # Retriever agreement
            "retriever_hits": retriever_hits,
            # Locality signals
            "seed_path_distance": seed_path_distance,
            "same_package": same_package,
            "package_distance": package_distance,
        })

    # Cross-encoder scoring (TinyBERT) — needed for training features
    coordinator = app_ctx.coordinator
    from coderecon.mcp.tools.recon.pipeline import _score_cross_encoder_tiny
    candidates_out = _score_cross_encoder_tiny(candidates_out, query, coordinator.db)

    # RRF fusion — attaches rrf_score to each candidate
    from coderecon.ranking.rrf import rrf_fuse
    candidates_out = rrf_fuse(candidates_out)

    elapsed_ms = round((time.monotonic() - t0) * 1000)

    # Query features
    query_features = {
        "query_len": len(query),
        "has_identifier": bool(parsed.explicit_symbols),
        "has_path": bool(parsed.explicit_paths),
        "identifier_density": (
            len(parsed.explicit_symbols) / max(len(query.split()), 1)
        ),
        "has_numbers": any(c.isdigit() for c in query),
        "has_quoted_strings": '"' in query or "'" in query,
        "term_count": len(parsed.primary_terms) + len(parsed.secondary_terms),
        # Task intent signals
        "intent": parsed.intent,
        "is_stacktrace_driven": parsed.is_stacktrace_driven,
        "is_test_driven": parsed.is_test_driven,
    }

    # Repo features
    coordinator = app_ctx.coordinator
    repo_features: dict[str, Any] = {}
    try:
        with coordinator.db.session() as session:
            from sqlmodel import func, select

            from coderecon.index.models import DefFact, File

            def_count = session.exec(select(func.count()).select_from(DefFact)).one()
            file_count = session.exec(select(func.count()).select_from(File)).one()
            repo_features = {
                "object_count": def_count,
                "file_count": file_count,
            }
    except (OSError, RuntimeError, ValueError):  # noqa: BLE001
        repo_features = {"object_count": 0, "file_count": 0}

    return {
        "query_features": query_features,
        "repo_features": repo_features,
        "candidates": candidates_out,
        "diagnostics": {
            "elapsed_ms": elapsed_ms,
            "candidate_count": len(candidates_out),
            "term_hits": sum(1 for c in merged.values() if c.from_term_match),
            "lex_hits": sum(1 for c in merged.values() if c.lex_hit_count > 0),
            "graph_hits": sum(1 for c in merged.values() if c.from_graph),
            "symbol_hits": sum(1 for c in merged.values() if c.from_explicit),
            "import_hits": sum(1 for c in merged.values() if c.import_direction is not None),
            "coverage_hits": sum(1 for c in merged.values() if c.from_coverage),
        },
    }


def register_raw_signals_tool(mcp: FastMCP, app_ctx: AppContext) -> None:
    """Register the recon_raw_signals tool with FastMCP server."""

    @mcp.tool(
        annotations={
            "title": "Recon: raw retrieval signals for ranking training",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
    async def recon_raw_signals(
        ctx: Context,  # noqa: ARG001
        query: str = Field(
            description=(
                "A single query string to evaluate against the current index. "
                "Returns raw per-def retrieval signals from all retrievers "
                "without any model inference, filtering, or ranking. "
                "Used for ranking model training data collection."
            ),
        ),
        seeds: list[str] = Field(
            default_factory=list,
            description="Symbol names to inject as explicit seed candidates.",
        ),
        pins: list[str] = Field(
            default_factory=list,
            description="File paths to inject as pinned candidates (all defs in each file).",
        ),
    ) -> dict[str, Any]:
        """Raw retrieval signals for ranking model training.

        Runs all retrievers (lexical, term match, graph,
        symbol) against the current index and returns the unfiltered
        candidate pool with per-retriever scores and ranks per DefFact.

        Does NOT run any model (ranker, cutoff, gate).
        Does NOT filter, sort, or truncate the candidate pool.
        Returns the raw union of all retriever outputs.
        """
        result = await raw_signals_pipeline(
            app_ctx, query,
            seeds=seeds or None,
            pins=pins or None,
        )

        from coderecon.mcp.delivery import wrap_response

        return wrap_response(
            result,
            resource_kind="raw_signals",
            session_id=ctx.session_id,
        )


# ---------------------------------------------------------------------------
# Locality helpers
# ---------------------------------------------------------------------------


def _shared_prefix_depth(a: str, b: str) -> int:
    """Count shared directory prefix segments between two paths."""
    a_parts = a.split("/")
    b_parts = b.split("/")
    shared = 0
    for pa, pb in zip(a_parts, b_parts):
        if pa == pb:
            shared += 1
        else:
            break
    return shared


def _min_path_distance(candidate_path: str, seed_paths: list[str]) -> int:
    """Minimum directory distance from candidate to any seed path.

    Distance = (candidate depth - shared) + (seed depth - shared).
    Returns 999 if no seeds (neutral default for model).
    """
    if not seed_paths or not candidate_path:
        return 999
    c_depth = candidate_path.count("/")
    best = 999
    for sp in seed_paths:
        shared = _shared_prefix_depth(candidate_path, sp)
        s_depth = sp.count("/")
        dist = (c_depth - shared) + (s_depth - shared)
        if dist < best:
            best = dist
    return best


def _min_package_distance(
    candidate_module: str, seed_modules: list[str]
) -> tuple[bool, int]:
    """Minimum package distance from candidate to any seed module.

    Returns (same_package: bool, distance: int).
    """
    if not seed_modules or not candidate_module:
        return False, 999
    c_parts = candidate_module.split(".")
    best_dist = 999
    same = False
    for sm in seed_modules:
        s_parts = sm.split(".")
        shared = 0
        for cp, sp in zip(c_parts, s_parts):
            if cp == sp:
                shared += 1
            else:
                break
        dist = (len(c_parts) - shared) + (len(s_parts) - shared)
        if dist < best_dist:
            best_dist = dist
        if shared > 0 and shared >= min(len(c_parts), len(s_parts)):
            same = True
    return same, best_dist
