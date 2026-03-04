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

from codeplane.mcp.tools.recon.harvesters import (
    _harvest_def_embedding,
    _harvest_explicit,
    _harvest_graph,
    _harvest_lexical,
    _harvest_term_match,
)
from codeplane.mcp.tools.recon.merge import (
    _enrich_candidates,
    _merge_candidates,
)
from codeplane.mcp.tools.recon.parsing import parse_task

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


async def _raw_signals_pipeline(
    app_ctx: AppContext,
    query: str,
) -> dict[str, Any]:
    """Run all retrievers and return raw per-def signals without model inference.

    Returns a dict with query features, repo features, and a list of
    candidate defs with per-retriever scores and ranks.
    """
    t0 = time.monotonic()

    # Parse query
    parsed = parse_task(query)

    # Run all harvesters in parallel-safe order
    # A: Embedding (per-def + per-file, both indices)
    emb_candidates = await _harvest_def_embedding(app_ctx, parsed, top_k=200)

    # B: Term match
    term_candidates = await _harvest_term_match(app_ctx, parsed)

    # C: Lexical
    lex_candidates = await _harvest_lexical(app_ctx, parsed)

    # D: Explicit (symbols/paths from query text, no agent seeds)
    explicit_candidates = await _harvest_explicit(app_ctx, parsed)

    # Merge A-D
    merged = _merge_candidates(emb_candidates, term_candidates, lex_candidates, explicit_candidates)

    # E: Graph walk from top merged candidates
    graph_candidates = await _harvest_graph(app_ctx, merged, parsed)

    # Final merge with graph
    merged = _merge_candidates(merged, graph_candidates)

    # Enrich: resolve missing DefFacts, populate metadata
    await _enrich_candidates(app_ctx, merged)

    # Build per-retriever score/rank lists
    # Embedding scores from evidence
    emb_scores: dict[str, float] = {}
    for uid, cand in emb_candidates.items():
        for ev in cand.evidence:
            if ev.category == "embedding":
                emb_scores[uid] = ev.score
                break

    # Term match scores
    term_scores: dict[str, float] = {
        uid: cand.term_idf_score for uid, cand in merged.items() if cand.term_idf_score > 0
    }

    # Lexical scores
    lex_scores: dict[str, float] = {
        uid: float(cand.lexical_hit_count) for uid, cand in merged.items() if cand.lexical_hit_count > 0
    }

    # Graph scores
    graph_scores: dict[str, float] = {
        uid: cand.graph_quality for uid, cand in merged.items() if cand.graph_quality > 0
    }

    # Symbol/explicit scores
    symbol_scores: dict[str, float] = {}
    for uid, cand in merged.items():
        if cand.from_explicit:
            # Use max evidence score from explicit category
            max_score = 0.0
            for ev in cand.evidence:
                if ev.category in ("explicit", "auto_seed"):
                    max_score = max(max_score, ev.score)
            if max_score > 0:
                symbol_scores[uid] = max_score

    # Compute ranks per retriever
    def _compute_ranks(scores: dict[str, float]) -> dict[str, int]:
        sorted_uids = sorted(scores.keys(), key=lambda u: -scores[u])
        return {uid: rank for rank, uid in enumerate(sorted_uids, 1)}

    emb_ranks = _compute_ranks(emb_scores)
    term_ranks = _compute_ranks(term_scores)
    lex_ranks = _compute_ranks(lex_scores)
    graph_ranks = _compute_ranks(graph_scores)
    symbol_ranks = _compute_ranks(symbol_scores)

    # Build candidate list
    candidates_out: list[dict[str, Any]] = []
    for uid, cand in merged.items():
        if cand.def_fact is None:
            continue

        d = cand.def_fact
        retriever_hits = sum([
            uid in emb_scores,
            uid in term_scores,
            uid in lex_scores,
            uid in graph_scores,
            uid in symbol_scores,
        ])

        candidates_out.append({
            "def_uid": uid,
            "path": cand.file_path,
            "kind": d.kind,
            "name": d.name,
            "start_line": d.start_line,
            "end_line": d.end_line,
            "object_size_lines": d.end_line - d.start_line + 1,
            "file_ext": "." + cand.file_path.rsplit(".", 1)[-1] if "." in cand.file_path else "",
            # Per-retriever scores (None if retriever didn't find this def)
            "emb_score": emb_scores.get(uid),
            "emb_rank": emb_ranks.get(uid),
            "lex_score": lex_scores.get(uid),
            "lex_rank": lex_ranks.get(uid),
            "term_score": term_scores.get(uid),
            "term_rank": term_ranks.get(uid),
            "graph_score": graph_scores.get(uid),
            "graph_rank": graph_ranks.get(uid),
            "symbol_score": symbol_scores.get(uid),
            "symbol_rank": symbol_ranks.get(uid),
            "retriever_hits": retriever_hits,
        })

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
    }

    # Repo features
    coordinator = app_ctx.coordinator
    repo_features: dict[str, Any] = {}
    try:
        with coordinator.db.session() as session:
            from sqlmodel import func, select

            from codeplane.index.models import DefFact, File

            def_count = session.exec(select(func.count()).select_from(DefFact)).one()
            file_count = session.exec(select(func.count()).select_from(File)).one()
            repo_features = {
                "object_count": def_count,
                "file_count": file_count,
            }
    except Exception:  # noqa: BLE001
        repo_features = {"object_count": 0, "file_count": 0}

    return {
        "query_features": query_features,
        "repo_features": repo_features,
        "candidates": candidates_out,
        "diagnostics": {
            "elapsed_ms": elapsed_ms,
            "candidate_count": len(candidates_out),
            "emb_hits": len(emb_scores),
            "term_hits": len(term_scores),
            "lex_hits": len(lex_scores),
            "graph_hits": len(graph_scores),
            "symbol_hits": len(symbol_scores),
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
    ) -> dict[str, Any]:
        """Raw retrieval signals for ranking model training.

        Runs all retrievers (embedding, lexical, term match, graph,
        symbol) against the current index and returns the unfiltered
        candidate pool with per-retriever scores and ranks per DefFact.

        Does NOT run any model (ranker, cutoff, gate).
        Does NOT filter, sort, or truncate the candidate pool.
        Returns the raw union of all retriever outputs.
        """
        result = await _raw_signals_pipeline(app_ctx, query)

        from codeplane.mcp.delivery import wrap_response

        return wrap_response(
            result,
            resource_kind="raw_signals",
            session_id=ctx.session_id,
        )
