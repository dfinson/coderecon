"""Recon pipeline — context retrieval + tool registration.

Two ranking paths:
  - **Model path** (when LightGBM models present): gate → file ranker →
    def ranker → cutoff.
  - **Heuristic path** (fallback): RRF fusion → file prune → elbow cutoff.

Also registers the ``recon``, ``recon_map``, and optionally
``recon_raw_signals`` MCP tools.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

from coderecon.config.constants import MS_PER_SEC

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from coderecon.index.db import Database
    from coderecon.mcp.context import AppContext

log = structlog.get_logger(__name__)

from coderecon.mcp.tools.recon.pipeline_scoring import (  # noqa: E402
    _build_ce_documents,  # noqa: F401  # re-exported for tests
    _read_signature,
    _read_snippet,
    _score_cross_encoder,
)


def _build_query_metrics(diagnostics: dict, candidates: list, seeds: list | None, pins: list | None) -> dict:
    """Build aggregate query metrics from diagnostics."""
    total = diagnostics.get("candidate_count", 0)
    metrics = {
        "total_candidates_scored": total,
        "retriever_coverage": {
            "term_match": diagnostics.get("term_hits", 0),
            "lexical": diagnostics.get("lex_hits", 0),
            "graph": diagnostics.get("graph_hits", 0),
            "symbol": diagnostics.get("symbol_hits", 0),
        },
    }
    if candidates:
        scores = [c.get("score", 0) for c in candidates]
        metrics["top_score"] = scores[0] if scores else 0
        metrics["score_p50"] = scores[len(scores) // 2] if scores else 0
        # Find largest score drop
        if len(scores) > 1:
            gaps = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
            max_gap_idx = max(range(len(gaps)), key=lambda i: gaps[i])
            metrics["score_drop_at"] = max_gap_idx + 1
        else:
            metrics["score_drop_at"] = 1
    if seeds:
        seed_hits = sum(1 for c in candidates if c.get("name") in seeds)
        metrics["seed_hit_rate"] = round(seed_hits / len(seeds), 2) if seeds else 0
    if pins:
        pin_paths = set(pins)
        pin_hits = len(pin_paths & {c.get("path") for c in candidates})
        metrics["pin_hit_rate"] = round(pin_hits / len(pins), 2) if pins else 0
    return metrics

def _build_hints(metrics: dict, gate_label: str) -> list[str]:
    """Generate actionable hints from query metrics."""
    hints: list[str] = []
    cov = metrics.get("retriever_coverage", {})
    if cov.get("lexical", 0) == 0:
        hints.append(
            "Lexical retriever returned 0 hits — your query has no literal code strings. "
            "Include error messages, log strings, or comments for better coverage."
        )
    if cov.get("term_match", 0) == 0:
        hints.append(
            "No term matches — none of your query words matched symbol names. "
            "Try including exact function/class names."
        )
    if cov.get("symbol", 0) == 0 and cov.get("graph", 0) == 0:
        hints.append(
            "No seeds or graph hits. Consider calling recon_map first to discover "
            "key symbols, then pass them as seeds."
        )
    drop = metrics.get("score_drop_at")
    if drop and drop <= 5:
        hints.append(
            f"Sharp score drop at position {drop} — top {drop} results are strongly "
            f"relevant, the rest is speculative."
        )
    seed_rate = metrics.get("seed_hit_rate")
    if seed_rate is not None and seed_rate < 0.5:
        hints.append(
            f"Only {int(seed_rate * 100)}% of seeds resolved — some seed names may not "
            f"exist in this repo. Check symbol names against recon_map."
        )
    return hints

def _models_available() -> bool:
    """Return True if all four ranking models are present on disk."""
    data_dir = Path(__file__).resolve().parents[3] / "ranking" / "data"
    return all(
        (data_dir / name).exists()
        for name in ("gate.lgbm", "file_ranker.lgbm", "ranker.lgbm", "cutoff.lgbm")
    )

async def recon_pipeline(
    app_ctx: AppContext,
    task: str,
    seeds: list[str] | None = None,
    pins: list[str] | None = None,
) -> dict[str, Any]:
    """Run the full recon pipeline: retrieve → rank → cut → snippets.
    If LightGBM models are present: gate → file_ranker → ranker → cutoff.
    Otherwise: RRF fusion → file prune → elbow cutoff (model-free).
    """
    from coderecon.mcp.tools.recon.raw_signals import raw_signals_pipeline
    t0 = time.monotonic()
    repo_root = app_ctx.coordinator.repo_root
    # 1. Get raw signals (includes CE scoring + RRF fusion)
    raw = await raw_signals_pipeline(app_ctx, task, seeds=seeds, pins=pins)
    candidates = raw.get("candidates", [])
    query_features = raw.get("query_features", {})
    repo_features = raw.get("repo_features", {})
    diagnostics = raw.get("diagnostics", {})
    if _models_available():
        return _pipeline_model(
            candidates, query_features, repo_features, diagnostics,
            repo_root=repo_root, seeds=seeds, pins=pins, t0=t0,
            task=task, db=app_ctx.coordinator.db,
        )
    log.info("ranking.heuristic_mode", reason="models_unavailable")
    return _pipeline_heuristic(
        candidates, diagnostics,
        repo_root=repo_root, seeds=seeds, pins=pins, t0=t0,
    )

def _pipeline_model(
    candidates: list[dict[str, Any]],
    query_features: dict[str, Any],
    repo_features: dict[str, Any],
    diagnostics: dict[str, Any],
    *,
    repo_root: Path,
    seeds: list[str] | None,
    pins: list[str] | None,
    t0: float,
    task: str,
    db: Database,
) -> dict[str, Any]:
    """Model path: gate → file ranker → def ranker → cutoff."""
    from coderecon.ranking.cutoff import load_cutoff
    from coderecon.ranking.features import (
        extract_cutoff_features,
        extract_file_ranker_features,
        extract_gate_features,
        extract_ranker_features,
    )
    from coderecon.ranking.file_ranker import load_file_ranker
    from coderecon.ranking.gate import load_gate
    from coderecon.ranking.models import GateLabel
    from coderecon.ranking.ranker import load_ranker
    # Gate
    gate = load_gate()
    gate_features = extract_gate_features(candidates, query_features, repo_features)
    gate_label = gate.classify(gate_features)
    if gate_label != GateLabel.OK:
        elapsed = round((time.monotonic() - t0) * MS_PER_SEC)
        metrics = {
            "scored": len(candidates),
            "term": diagnostics.get("term_hits", 0),
            "lex": diagnostics.get("lex_hits", 0),
            "graph": diagnostics.get("graph_hits", 0),
            "sym": diagnostics.get("symbol_hits", 0),
            "ms": elapsed,
        }
        hints = _build_hints(
            _build_query_metrics(diagnostics, [], seeds, pins),
            gate_label.value,
        )[:3]
        return {
            "gate": gate_label.value,
            "results": [],
            "n": 0,
            "metrics": metrics,
            "hints": hints,
        }
    # CE scoring already done in raw_signals_pipeline; proceed to file ranking
    # File Ranking (Stage 1) — prune to top files
    file_ranker = load_file_ranker()
    file_features, file_to_candidates = extract_file_ranker_features(
        candidates, query_features,
    )
    file_scores = file_ranker.score(file_features)
    scored_files = sorted(
        zip(file_features, file_scores, strict=True), key=lambda x: -x[1],
    )
    # TODO: Replace with elbow/gap-based cutoff on file_scores. Hardcoded 20
    # silently drops files that could contain relevant defs — caps recall.
    max_files = min(20, len(scored_files))
    top_file_paths = {ff["_path"] for ff, _ in scored_files[:max_files]}
    for c in candidates:
        if c.get("symbol_source") in ("pin", "agent_seed"):
            top_file_paths.add(c.get("path", ""))
    filtered_candidates = [c for c in candidates if c.get("path", "") in top_file_paths]
    # Cross-encoder scoring (adds ce_score to each candidate)
    filtered_candidates = _score_cross_encoder(filtered_candidates, task, db)
    # Def Ranking (Stage 2)
    ranker = load_ranker()
    ranker_features = extract_ranker_features(filtered_candidates, query_features)
    scores = ranker.score(ranker_features)
    scored = sorted(zip(filtered_candidates, scores, strict=True), key=lambda x: -x[1])
    # Cutoff
    cutoff = load_cutoff()
    ranked_for_cutoff = [{**c, "ranker_score": s} for c, s in scored]
    cutoff_features = extract_cutoff_features(
        ranked_for_cutoff, query_features, repo_features,
    )
    predicted_n = cutoff.predict(cutoff_features)
    # Build output
    return _build_output(
        scored, predicted_n,
        candidates=candidates, diagnostics=diagnostics,
        repo_root=repo_root, seeds=seeds, pins=pins,
        gate_label=gate_label.value, t0=t0,
        extra_metrics={"files_scored": len(file_features), "files_kept": len(top_file_paths),
                       "defs_ranked": len(filtered_candidates)},
    )

def _pipeline_heuristic(
    candidates: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    *,
    repo_root: Path,
    seeds: list[str] | None,
    pins: list[str] | None,
    t0: float,
) -> dict[str, Any]:
    """Heuristic path: RRF fusion → file prune → elbow cutoff."""
    from coderecon.ranking.elbow import elbow_cut
    from coderecon.ranking.rrf import rrf_file_prune
    # Candidates already have rrf_score from shared layer; just prune + cut
    fused = sorted(candidates, key=lambda c: -c.get("rrf_score", 0.0))
    # File-level prune
    pinned_paths = set(pins) if pins else set()
    pruned = rrf_file_prune(fused, pinned_paths=pinned_paths)
    # Elbow cutoff on RRF scores
    rrf_scores = [c["rrf_score"] for c in pruned]
    predicted_n = elbow_cut(rrf_scores)
    # Build (candidate, score) pairs for output builder
    scored = [(c, c["rrf_score"]) for c in pruned]
    return _build_output(
        scored, predicted_n,
        candidates=candidates, diagnostics=diagnostics,
        repo_root=repo_root, seeds=seeds, pins=pins,
        gate_label="OK", t0=t0,
        extra_metrics={"strategy": "heuristic"},
    )

def _build_output(
    scored: list[tuple[dict[str, Any], float]],
    predicted_n: int,
    *,
    candidates: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    repo_root: Path,
    seeds: list[str] | None,
    pins: list[str] | None,
    gate_label: str,
    t0: float,
    extra_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the final output dict shared by both pipeline paths."""
    top_n = scored[:predicted_n]
    full_snippet_count = max(1, predicted_n // 2)
    result_candidates = []
    for idx, (c, s) in enumerate(top_n):
        loc = f"{c['kind']} {c['name']} {c['path']}:{c['start_line']}-{c['end_line']}"
        entry: dict[str, Any] = {
            "loc": loc,
            "score": round(s, 4),
        }
        if idx < full_snippet_count:
            snippet = _read_snippet(repo_root, c["path"], c["start_line"], c["end_line"])
            entry["snippet"] = snippet or ""
        else:
            sig = _read_signature(repo_root, c["path"], c["start_line"], c["end_line"])
            entry["sig"] = sig or ""
        result_candidates.append(entry)
    elapsed = round((time.monotonic() - t0) * MS_PER_SEC)
    metrics: dict[str, Any] = {
        "scored": len(candidates),
        "term": diagnostics.get("term_hits", 0),
        "lex": diagnostics.get("lex_hits", 0),
        "graph": diagnostics.get("graph_hits", 0),
        "sym": diagnostics.get("symbol_hits", 0),
        "ms": elapsed,
    }
    if extra_metrics:
        metrics.update(extra_metrics)
    hints = _build_hints(
        _build_query_metrics(diagnostics, result_candidates, seeds, pins),
        gate_label,
    )[:3]
    return {
        "gate": gate_label,
        "results": result_candidates,
        "n": predicted_n,
        "metrics": metrics,
        "hints": hints,
    }

async def recon_map_core(app_ctx: AppContext) -> dict[str, Any]:
    """Repository structure map (transport-agnostic)."""
    repo_map: dict[str, Any] = {}
    try:
        map_result = await app_ctx.coordinator.map_repo(
            include=["structure", "languages", "entry_points"],
            depth=3,
            limit=100,
        )
        from coderecon.mcp.tools.index import _build_overview, _map_repo_sections_to_text
        repo_map = {
            "overview": _build_overview(map_result),
            **_map_repo_sections_to_text(map_result),
        }
        try:
            from coderecon.index.analysis.code_graph import (
                build_def_graph,
                build_file_graph,
                compute_file_pagerank,
                compute_pagerank,
            )
            engine = app_ctx.coordinator.db.engine
            fg = build_file_graph(engine)
            if fg.number_of_nodes() > 0:
                top_files = compute_file_pagerank(fg, top_k=10)
                repo_map["pagerank_files"] = [
                    {"path": path, "score": round(score, 6)}
                    for path, score in top_files
                ]
            dg = build_def_graph(engine)
            if dg.number_of_nodes() > 0:
                top_defs = compute_pagerank(dg, top_k=10)
                repo_map["pagerank_defs"] = [
                    {
                        "def_uid": s.def_uid,
                        "name": s.name,
                        "kind": s.kind,
                        "file": s.file_path,
                        "score": round(s.pagerank, 6),
                    }
                    for s in top_defs
                ]
        except (ImportError, OSError, ValueError):  # pagerank is optional
            log.debug("recon_map.pagerank_skipped", exc_info=True)
    except (ImportError, OSError, ValueError, AttributeError):
        log.warning("recon_map.failed", exc_info=True)
        repo_map = {"error": "Failed to build repo map"}
    from coderecon.mcp.delivery import wrap_response
    return wrap_response(repo_map, resource_kind="repo_map")

def register_tools(mcp: FastMCP, app_ctx: AppContext, *, dev_mode: bool = False) -> None:
    """Register recon tools with FastMCP server."""
    # Register raw signals endpoint only in dev mode (ranking training)
    if dev_mode:
        from coderecon.mcp.tools.recon.raw_signals import register_raw_signals_tool
        register_raw_signals_tool(mcp, app_ctx)
    @mcp.tool(
        annotations={
            "title": "Recon: task-aware context retrieval",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
    async def recon(
        ctx: Context,  # noqa: ARG001
        task: str = Field(
            description=(
                "Natural language description of the task. "
                "Be specific: include symbol names, file paths, "
                "or domain terms when known."
            ),
        ),
        seeds: list[str] = Field(
            default_factory=list,
            description=(
                "Symbol names to seed retrieval with "
                "(e.g., ['IndexCoordinatorEngine', 'FactQueries'])."
            ),
        ),
        pins: list[str] = Field(
            default_factory=list,
            description=(
                "File paths to pin as relevant "
                "(e.g., ['src/core/base_model.py'])."
            ),
        ),
    ) -> dict[str, Any]:
        """Task-aware context retrieval — returns ranked semantic spans with code.
        Pipeline: retrieve → gate → rank → cutoff → snippet extraction.
        Top half of results include full code snippets. Bottom half
        include signature + docstring only. Includes query metrics
        and actionable hints for improving retrieval.
        """
        recon_id = uuid.uuid4().hex[:12]
        result = await recon_pipeline(
            app_ctx, task,
            seeds=seeds or None,
            pins=pins or None,
        )
        gate = result["gate"]
        results = result["results"]
        metrics = result.get("metrics", {})
        hints = result.get("hints", [])
        response: dict[str, Any] = {
            "recon_id": recon_id,
            "gate": gate,
            "results": results,
            "metrics": metrics,
        }
        if gate == "OK" and results:
            # Extract unique file paths from loc strings
            paths = []
            for r in results[:8]:
                loc = r.get("loc", "")
                # loc format: "kind name path:start-end"
                parts = loc.rsplit(":", 1)
                if parts:
                    path = parts[0].rsplit(" ", 1)[-1] if " " in parts[0] else parts[0]
                    if path not in paths:
                        paths.append(path)
            n_full = sum(1 for r in results if "snippet" in r)
            n_sig = sum(1 for r in results if "sig" in r)
            hint = (
                f"{len(results)} spans ({n_full} full, {n_sig} sig-only) "
                f"in {', '.join(paths)}."
            )
            if hints:
                hint += " " + " ".join(hints[:2])
            response["hint"] = hint
        elif gate != "OK":
            gate_msg = {
                "UNSAT": "Wrong assumptions about this repo.",
                "BROAD": "Too broad — decompose.",
                "AMBIG": "Ambiguous — specify subsystem.",
            }
            hint = gate_msg.get(gate, "Retry.")
            if hints:
                hint += " " + hints[0]
            response["hint"] = hint
        from coderecon.mcp.delivery import wrap_response
        return wrap_response(
            response,
            resource_kind="recon_result",
            session_id=ctx.session_id,
        )
    @mcp.tool(
        annotations={
            "title": "Recon: repository structure map",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
    async def recon_map(
        ctx: Context,  # noqa: ARG001
    ) -> dict[str, Any]:
        """Repository structure map — file tree, languages, entry points."""
        return await recon_map_core(app_ctx)
