"""Offline ranking solver — scores pre-collected candidates from merged parquet.

Inspect AI solver that replaces the live ``raw_signals_pipeline`` with
a lookup into ``candidates_rank.parquet``.  Applies the same trained
gate / ranker / cutoff models, produces the same ``state.store`` keys,
so the existing ``ranking_scorer`` and ``gate_scorer`` work unchanged.

Used by the ``micro-eval`` stage: fast offline sanity check that the
models learned something, without loading any repo index or daemon.

Baseline arms:
- ``offline_rrf_solver`` — rank by RRF score only, no learned models.
- ``offline_ce_only_solver`` — rank by cross-encoder (TinyBERT) score only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from inspect_ai.solver import Solver, TaskState, solver

from recon_lab.schema import OK_QUERY_TYPES
from recon_lab.training.train_all import (
    CUTOFF_FEATURES,
    DEF_RANKER_FEATURES,
    GATE_FEATURES,
    GATE_LABELS,
    _LOAD_COLS,
    _compute_gate_features,
    _cutoff_features_from_scores,
    _prepare_def_features,
)

log = logging.getLogger(__name__)

_GATE_LABEL_INV = {v: k for k, v in GATE_LABELS.items()}


class _OfflineRankingPipeline:
    """Pre-loads merged parquet + models, serves per-query lookups."""

    def __init__(self, merged_dir: str, models_dir: str) -> None:
        self._merged_dir = Path(merged_dir).expanduser()
        self._models_dir = Path(models_dir).expanduser()
        self._df: pd.DataFrame | None = None
        self._ranker: lgb.Booster | None = None
        self._gate: lgb.Booster | None = None
        self._cutoff: lgb.Booster | None = None

    def _ensure_loaded(self) -> None:
        if self._df is not None:
            return

        pq_path = self._merged_dir / "candidates_rank.parquet"
        available = set(pq.ParquetFile(pq_path).schema_arrow.names)
        load_cols = [c for c in _LOAD_COLS if c in available]
        self._df = pq.read_table(pq_path, columns=load_cols).to_pandas()
        self._df = _prepare_def_features(self._df)

        # Candidate key matching GT format
        self._df["candidate_key"] = (
            self._df["path"].astype(str) + ":" +
            self._df["kind"].fillna("").astype(str) + ":" +
            self._df["name"].fillna("").astype(str) + ":" +
            self._df["start_line"].fillna(0).astype(int).astype(str)
        )

        # Pre-score all candidates with the def ranker
        self._ranker = lgb.Booster(model_file=str(self._models_dir / "def_ranker.lgbm"))
        X = self._df[DEF_RANKER_FEATURES].fillna(0).values
        self._df["ranker_score"] = self._ranker.predict(X)

        self._gate = lgb.Booster(model_file=str(self._models_dir / "gate.lgbm"))
        self._cutoff = lgb.Booster(model_file=str(self._models_dir / "cutoff.lgbm"))

        # Index by (run_id, query_id) for fast lookup
        self._df["_lookup"] = self._df["run_id"].astype(str) + "|" + self._df["query_id"].astype(str)
        self._groups = dict(list(self._df.groupby("_lookup", sort=False)))

        log.info("Offline pipeline loaded: %d candidates, %d query groups",
                 len(self._df), len(self._groups))

    def infer(self, meta: dict) -> dict:
        """Look up pre-scored candidates and apply gate + ranker + cutoff."""
        self._ensure_loaded()
        assert self._gate is not None and self._cutoff is not None

        # Find the query's candidates in the merged parquet
        # query_id in the dataset matches the parquet's query_id
        query_id = meta.get("query_id", "")
        repo_id = meta.get("repo_id", "")
        task_id = meta.get("task_id", "")

        # Try exact run_id|query_id lookup
        run_id = f"{repo_id}_{task_id}" if task_id and task_id != "__non_ok" else f"{repo_id}__non_ok"
        lookup_key = f"{run_id}|{query_id}"
        qdf = self._groups.get(lookup_key)

        if qdf is None or qdf.empty:
            log.warning("No candidates for %s — returning empty", lookup_key)
            return {
                "ranked_candidate_keys": [],
                "predicted_relevances": [],
                "predicted_n": 0,
                "predicted_gate": "OK",
            }

        # Gate
        gate_feat = _compute_gate_features(qdf)
        gate_x = np.array([[gate_feat.get(f, 0) for f in GATE_FEATURES]], dtype=float)
        gate_probs = self._gate.predict(gate_x)[0]
        pred_gate = _GATE_LABEL_INV[int(np.argmax(gate_probs))]

        if pred_gate != "OK":
            return {
                "ranked_candidate_keys": [],
                "predicted_relevances": [],
                "predicted_n": 0,
                "predicted_gate": pred_gate,
            }

        # Rank by model score
        ranked = qdf.sort_values("ranker_score", ascending=False)
        ranked_keys = ranked["candidate_key"].tolist()
        scores = ranked["ranker_score"].values

        # Cutoff
        cutoff_feat = _cutoff_features_from_scores(
            scores,
            ranked["retriever_hits"].fillna(0).values,
            query_len=int(qdf["query_len"].iloc[0]),
            has_identifier=bool(qdf["has_identifier"].iloc[0]),
            has_path=bool(qdf["has_path"].iloc[0]),
            has_numbers=bool(qdf.get("has_numbers", pd.Series([False])).iloc[0]),
            has_quoted_strings=bool(qdf.get("has_quoted_strings", pd.Series([False])).iloc[0]),
            is_stacktrace_driven=bool(qdf.get("is_stacktrace_driven", pd.Series([False])).iloc[0]),
            is_test_driven=bool(qdf.get("is_test_driven", pd.Series([False])).iloc[0]),
            object_count=int(qdf.get("object_count", pd.Series([0])).iloc[0]),
            file_count=int(qdf.get("file_count", pd.Series([0])).iloc[0]),
            n_candidates=len(qdf),
            from_term_match=ranked["from_term_match"].fillna(False).values,
            from_graph=ranked["from_graph"].fillna(False).values,
            from_explicit=ranked["from_explicit"].fillna(False).values,
            splade_scores=ranked.get("splade_score", pd.Series(dtype=float)).fillna(0).values,
        )
        cutoff_x = np.array([[cutoff_feat.get(f, 0) for f in CUTOFF_FEATURES]], dtype=float)
        predicted_n = max(1, int(round(self._cutoff.predict(cutoff_x)[0])))

        return {
            "ranked_candidate_keys": ranked_keys,
            "predicted_relevances": [round(float(s), 4) for s in scores],
            "predicted_n": predicted_n,
            "predicted_gate": pred_gate,
        }


@solver
def offline_ranking_solver(
    merged_dir: str = "~/.recon/recon-lab/data/merged",
    models_dir: str = "~/.recon/recon-lab/models",
) -> Solver:
    """Inspect AI solver: offline scoring from merged parquet."""
    pipeline = _OfflineRankingPipeline(merged_dir=merged_dir, models_dir=models_dir)

    async def solve(state: TaskState, generate: Any) -> TaskState:
        meta = state.metadata
        result = pipeline.infer(meta)
        state.store.set("ranked_candidate_keys", result["ranked_candidate_keys"])
        state.store.set("predicted_relevances", result["predicted_relevances"])
        state.store.set("predicted_n", result["predicted_n"])
        state.store.set("predicted_gate", result["predicted_gate"])
        return state

    return solve


# ═══════════════════════════════════════════════════════════════════
# Baseline arms — no learned models, pure signal ranking
# ═══════════════════════════════════════════════════════════════════


class _BaselineRankingPipeline:
    """Ranks candidates by a single score column — no LGBM models.

    Uses a fixed cutoff of *default_n* (returns top-N for every query)
    and always predicts gate=OK, isolating the ranking signal.
    """

    def __init__(
        self,
        merged_dir: str,
        score_column: str,
        default_n: int,
    ) -> None:
        self._merged_dir = Path(merged_dir).expanduser()
        self._score_column = score_column
        self._default_n = default_n
        self._df: pd.DataFrame | None = None
        self._groups: dict[str, pd.DataFrame] | None = None

    def _ensure_loaded(self) -> None:
        if self._df is not None:
            return

        pq_path = self._merged_dir / "candidates_rank.parquet"
        available = set(pq.ParquetFile(pq_path).schema_arrow.names)
        load_cols = [c for c in _LOAD_COLS if c in available]
        self._df = pq.read_table(pq_path, columns=load_cols).to_pandas()
        self._df = _prepare_def_features(self._df)

        self._df["candidate_key"] = (
            self._df["path"].astype(str) + ":" +
            self._df["kind"].fillna("").astype(str) + ":" +
            self._df["name"].fillna("").astype(str) + ":" +
            self._df["start_line"].fillna(0).astype(int).astype(str)
        )

        self._df["_lookup"] = (
            self._df["run_id"].astype(str) + "|" + self._df["query_id"].astype(str)
        )
        self._groups = dict(list(self._df.groupby("_lookup", sort=False)))

        log.info(
            "Baseline pipeline loaded (%s): %d candidates, %d groups",
            self._score_column, len(self._df), len(self._groups),
        )

    def infer(self, meta: dict) -> dict:
        self._ensure_loaded()
        assert self._groups is not None

        query_id = meta.get("query_id", "")
        repo_id = meta.get("repo_id", "")
        task_id = meta.get("task_id", "")

        run_id = (
            f"{repo_id}_{task_id}"
            if task_id and task_id != "__non_ok"
            else f"{repo_id}__non_ok"
        )
        lookup_key = f"{run_id}|{query_id}"
        qdf = self._groups.get(lookup_key)

        if qdf is None or qdf.empty:
            return {
                "ranked_candidate_keys": [],
                "predicted_relevances": [],
                "predicted_n": 0,
                "predicted_gate": "OK",
            }

        score_col = self._score_column
        scores = qdf[score_col].fillna(0.0)
        ranked = qdf.assign(_score=scores).sort_values("_score", ascending=False)
        ranked_keys = ranked["candidate_key"].tolist()
        ranked_scores = ranked["_score"].tolist()

        return {
            "ranked_candidate_keys": ranked_keys,
            "predicted_relevances": [round(float(s), 4) for s in ranked_scores],
            "predicted_n": min(self._default_n, len(ranked_keys)),
            "predicted_gate": "OK",
        }


@solver
def offline_rrf_solver(
    merged_dir: str = "~/.recon/recon-lab/data/merged",
    default_n: int = 10,
) -> Solver:
    """Baseline: rank by RRF score only, fixed cutoff, no learned models."""
    pipeline = _BaselineRankingPipeline(
        merged_dir=merged_dir,
        score_column="rrf_score",
        default_n=default_n,
    )

    async def solve(state: TaskState, generate: Any) -> TaskState:
        result = pipeline.infer(state.metadata)
        state.store.set("ranked_candidate_keys", result["ranked_candidate_keys"])
        state.store.set("predicted_relevances", result["predicted_relevances"])
        state.store.set("predicted_n", result["predicted_n"])
        state.store.set("predicted_gate", result["predicted_gate"])
        return state

    return solve


@solver
def offline_ce_only_solver(
    merged_dir: str = "~/.recon/recon-lab/data/merged",
    default_n: int = 10,
) -> Solver:
    """Baseline: rank by cross-encoder (TinyBERT) score only, fixed cutoff."""
    pipeline = _BaselineRankingPipeline(
        merged_dir=merged_dir,
        score_column="ce_score_tiny",
        default_n=default_n,
    )

    async def solve(state: TaskState, generate: Any) -> TaskState:
        result = pipeline.infer(state.metadata)
        state.store.set("ranked_candidate_keys", result["ranked_candidate_keys"])
        state.store.set("predicted_relevances", result["predicted_relevances"])
        state.store.set("predicted_n", result["predicted_n"])
        state.store.set("predicted_gate", result["predicted_gate"])
        return state

    return solve
