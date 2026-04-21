"""In-process ranking solver — runs the full pipeline without a daemon.

Inspect AI solver that wraps the ranking pipeline.

Supports two modes:

- **baseline** — no learned models; uses RRF heuristic for ranking,
  always predicts ``GateLabel.OK``, and returns a fixed cutoff of 20.
- **ranking** — loads trained LightGBM models (gate, ranker, cutoff)
  from *models_dir* and applies the full inference pipeline.

Loads each repo's index in-process via ``AppContext``, then runs
raw_signals → gate → ranker → cutoff.  Caches one ``AppContext`` at a
time (repos are processed sequentially).
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from pathlib import Path
from typing import Any

from inspect_ai.solver import Solver, TaskState, solver


def _def_key(c: dict) -> str:
    """Canonical candidate key matching ground truth format."""
    return f"{c.get('path', '')}:{c.get('kind', '')}:{c.get('name', '')}:{c.get('start_line', 0)}"


class _RankingPipeline:
    """Shared state for the ranking pipeline across samples."""

    def __init__(
        self,
        clone_dir: str,
        mode: str,
        models_dir: str,
        variant: str,
        *,
        diagnostic: bool = False,
    ) -> None:
        self._instances_dir = Path(clone_dir).expanduser()
        self._clones_dir = self._instances_dir.parent  # ../clones/
        self._mode = mode
        self._models_dir = Path(models_dir).expanduser()
        self._variant = variant
        self._diagnostic = diagnostic
        self._cached_repo: str | None = None
        self._ctx: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._gate: Any = None
        self._ranker: Any = None
        self._cutoff: Any = None

    def _workspace_id(self, instance_id: str) -> str:
        return "".join(c if c.isalnum() else "_" for c in instance_id)

    def _ensure_context(self, instance_id: str) -> None:
        if self._cached_repo == instance_id and self._ctx is not None:
            return

        if self._ctx is not None:
            self._ctx.coordinator.close()
            self._ctx = None
            self._cached_repo = None
            gc.collect()

        from coderecon.mcp.context import AppContext

        wid = self._workspace_id(instance_id)
        instance_dir = self._instances_dir / wid

        # index.db lives on the main repo clone, not the instance worktree
        from cpl_lab.data_manifest import main_clone_dir_for_dir

        data_root = self._clones_dir.parent / "data"
        data_dir = data_root / instance_id
        main_dir = main_clone_dir_for_dir(data_dir, self._clones_dir)
        if main_dir is None:
            msg = f"Cannot resolve main clone for {instance_id!r}"
            raise FileNotFoundError(msg)
        cp = main_dir / ".recon"
        if not cp.exists():
            msg = f"No coderecon index at {cp} (instance {instance_id!r})"
            raise FileNotFoundError(msg)

        repo_root = instance_dir if instance_dir.exists() else main_dir

        logging.disable(logging.WARNING)

        self._ctx = AppContext.standalone(
            repo_root=repo_root,
            db_path=cp / "index.db",
            tantivy_path=cp / "tantivy",
        )

        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

        self._loop.run_until_complete(self._ctx.coordinator.load_existing())
        self._cached_repo = instance_id

        from coderecon.ranking.cutoff import load_cutoff
        from coderecon.ranking.gate import load_gate
        from coderecon.ranking.ranker import load_ranker

        if self._mode == "ranking":
            v = self._variant
            gate_path = self._models_dir / f"gate_{v}.lgbm"
            ranker_path = self._models_dir / f"def_ranker_{v}.lgbm"
            cutoff_path = self._models_dir / f"cutoff_{v}.lgbm"
            if not gate_path.exists():
                gate_path = self._models_dir / "gate.lgbm"
            if not ranker_path.exists():
                ranker_path = self._models_dir / "ranker.lgbm"
            if not cutoff_path.exists():
                cutoff_path = self._models_dir / "cutoff.lgbm"
            self._gate = load_gate(gate_path)
            self._ranker = load_ranker(ranker_path)
            self._cutoff = load_cutoff(cutoff_path)
        else:
            _dummy = Path("/dev/null/no_model.lgbm")
            self._gate = load_gate(_dummy)
            self._ranker = load_ranker(_dummy)
            self._cutoff = load_cutoff(_dummy)

    def infer(self, meta: dict) -> dict:
        """Run the full pipeline for a single query record."""
        instance_id = meta.get("task_id") or meta["repo_id"]
        query = meta.get("problem_statement") or meta["query_text"]
        seeds = meta.get("seeds") or None
        pins = meta.get("pins") or None

        self._ensure_context(instance_id)
        assert self._ctx is not None
        assert self._loop is not None

        from coderecon.mcp.tools.recon.raw_signals import raw_signals_pipeline

        t0 = time.monotonic()
        raw = self._loop.run_until_complete(
            raw_signals_pipeline(self._ctx, query, seeds=seeds, pins=pins),
        )
        candidates = raw.get("candidates", [])
        query_features = raw.get("query_features", {})
        repo_features = raw.get("repo_features", {})
        latency_sec = round(time.monotonic() - t0, 3)

        if self._diagnostic:
            return self._infer_diagnostic(
                candidates, meta, pins, latency_sec,
            )

        from coderecon.ranking.features import (
            extract_cutoff_features,
            extract_gate_features,
            extract_ranker_features,
        )
        from coderecon.ranking.models import GateLabel

        gate_features = extract_gate_features(candidates, query_features, repo_features)
        gate_label = self._gate.classify(gate_features)

        if gate_label != GateLabel.OK:
            return {
                "ranked_candidate_keys": [],
                "predicted_relevances": [],
                "predicted_n": 0,
                "predicted_gate": gate_label.value,
            }

        ranker_features = extract_ranker_features(candidates, query_features)
        scores = self._ranker.score(ranker_features)
        scored = sorted(zip(candidates, scores, strict=False), key=lambda x: -x[1])

        ranked_for_cutoff = [{**c, "ranker_score": s} for c, s in scored]
        cutoff_features = extract_cutoff_features(
            ranked_for_cutoff, query_features, repo_features,
        )
        predicted_n = self._cutoff.predict(cutoff_features)

        ranked_candidate_keys = [_def_key(c) for c, _ in scored]
        predicted_relevances = [round(s, 4) for _, s in scored]

        return {
            "ranked_candidate_keys": ranked_candidate_keys,
            "predicted_relevances": predicted_relevances,
            "predicted_n": predicted_n,
            "predicted_gate": gate_label.value,
        }

    # ── Diagnostic path ───────────────────────────────────────────────

    def _infer_diagnostic(
        self,
        candidates: list[dict[str, Any]],
        meta: dict,
        pins: list[str] | None,
        latency_sec: float,
    ) -> dict:
        """RRF diagnostic: pool recall, per-harvester recall, prune loss, per-list data."""
        from coderecon.ranking.rrf import (
            build_named_rank_lists,
            rrf_file_prune,
            rrf_fuse,
        )

        pool_keys = [_def_key(c) for c in candidates]

        per_harvester_keys = {
            "term_match": [_def_key(c) for c in candidates if c.get("from_term_match")],
            "explicit": [_def_key(c) for c in candidates if c.get("from_explicit")],
            "graph": [_def_key(c) for c in candidates if c.get("from_graph")],
            "import": [
                _def_key(c) for c in candidates
                if c.get("import_direction") is not None
            ],
            "splade": [
                _def_key(c) for c in candidates
                if (c.get("splade_score") or 0) > 0
            ],
            "coverage": [_def_key(c) for c in candidates if c.get("from_coverage")],
        }

        # RRF fusion (sets rrf_score on each candidate dict)
        fused = rrf_fuse(candidates)

        # File prune
        pinned = set(pins or [])
        pruned = rrf_file_prune(fused, pinned_paths=pinned)
        post_prune_keys = [_def_key(c) for c in pruned]

        # Per-list orderings for solo/LOO ablation
        named_lists = build_named_rank_lists(candidates)
        per_list_ordered_keys = {
            name: [_def_key(candidates[idx]) for idx in indices]
            for name, indices in named_lists
        }

        ranked_candidate_keys = [_def_key(c) for c in fused]

        return {
            "ranked_candidate_keys": ranked_candidate_keys,
            "predicted_relevances": [
                round(c.get("rrf_score", 0.0), 6) for c in fused
            ],
            "predicted_n": 20,
            "predicted_gate": "OK",
            # Diagnostic fields
            "pool_candidate_keys": pool_keys,
            "per_harvester_keys": per_harvester_keys,
            "post_prune_keys": post_prune_keys,
            "per_list_ordered_keys": per_list_ordered_keys,
            "pool_size": len(candidates),
            "post_prune_pool_size": len(pruned),
            "latency_sec": latency_sec,
            "repo_id": meta.get("repo_id", ""),
        }


@solver
def ranking_solver(
    clone_dir: str = "~/.recon/recon-lab/clones/instances",
    mode: str = "baseline",
    models_dir: str = "~/.recon/recon-lab/models",
    variant: str = "structural",
) -> Solver:
    """Inspect AI solver that runs the in-process ranking pipeline."""
    pipeline = _RankingPipeline(
        clone_dir=clone_dir,
        mode=mode,
        models_dir=models_dir,
        variant=variant,
    )

    async def solve(state: TaskState, generate: Any) -> TaskState:
        meta = state.metadata
        result = pipeline.infer(meta)
        state.store.set("ranked_candidate_keys", result["ranked_candidate_keys"])
        state.store.set("predicted_relevances", result["predicted_relevances"])
        state.store.set("predicted_n", result["predicted_n"])
        state.store.set("predicted_gate", result["predicted_gate"])
        return state

    return solve


@solver
def diagnostic_ranking_solver(
    clone_dir: str = "~/.recon/recon-lab/clones/instances",
    models_dir: str = "~/.recon/recon-lab/models",
    variant: str = "structural",
) -> Solver:
    """Diagnostic solver: RRF pipeline with full funnel instrumentation."""
    pipeline = _RankingPipeline(
        clone_dir=clone_dir,
        mode="baseline",
        models_dir=models_dir,
        variant=variant,
        diagnostic=True,
    )

    async def solve(state: TaskState, generate: Any) -> TaskState:
        meta = state.metadata
        result = pipeline.infer(meta)
        for key, value in result.items():
            state.store.set(key, value)
        return state

    return solve

