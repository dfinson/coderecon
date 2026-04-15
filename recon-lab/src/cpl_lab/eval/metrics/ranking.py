"""Ranking scorer — NDCG, Hit@K, Cutoff F1/Precision/Recall.

Inspect AI scorer for the ranking pipeline evaluation.

Evaluates the ranker's ranked list quality and the cutoff model's
set-selection quality against def-level ground truth.
"""

from __future__ import annotations

import math
import statistics

from inspect_ai.scorer import (
    Metric,
    Score,
    Scorer,
    Target,
    metric,
    scorer,
)
from inspect_ai.solver import TaskState


def _dcg(relevances: list[float], k: int | None = None) -> float:
    if k is not None:
        relevances = relevances[:k]
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances))


def _ndcg(
    predicted_relevances: list[float], ideal_relevances: list[float], k: int | None = None
) -> float:
    ideal = _dcg(sorted(ideal_relevances, reverse=True), k)
    if ideal == 0:
        return 0.0
    return _dcg(predicted_relevances, k) / ideal


def _prf(returned: set[str], gt: set[str]) -> dict[str, float]:
    tp = len(returned & gt)
    p = tp / len(returned) if returned else 0.0
    r = tp / len(gt) if gt else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


# ── Custom metrics for aggregation ────────────────────────────────────────
# Defined before the scorer so they can be referenced in @scorer(metrics=[...])


def _avg_metadata_field(scores: list[Score], field: str) -> float:
    values = [s.metadata[field] for s in scores if s.metadata and field in s.metadata]
    return round(statistics.mean(values), 4) if values else 0.0


@metric
def metric_avg_ndcg_10() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "ndcg_10")
    return compute


@metric
def metric_avg_ndcg_5() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "ndcg_5")
    return compute


@metric
def metric_avg_ndcg_20() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "ndcg_20")
    return compute


@metric
def metric_avg_hit_5() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "hit_5")
    return compute


@metric
def metric_avg_hit_10() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "hit_10")
    return compute


@metric
def metric_avg_cutoff_f1() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "cutoff_f1")
    return compute


@metric
def metric_avg_cutoff_precision() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "cutoff_precision")
    return compute


@metric
def metric_avg_cutoff_recall() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "cutoff_recall")
    return compute


@metric
def metric_p95_latency() -> Metric:
    def compute(scores: list[Score]) -> float:
        latencies = sorted(
            s.metadata["latency_sec"] for s in scores
            if s.metadata and "latency_sec" in s.metadata
        )
        if not latencies:
            return 0.0
        p95_idx = max(0, math.ceil(0.95 * len(latencies)) - 1)
        return round(latencies[p95_idx], 3)
    return compute


# ── Scorer ────────────────────────────────────────────────────────────────


@scorer(metrics=[
    metric_avg_ndcg_5(),
    metric_avg_ndcg_10(),
    metric_avg_ndcg_20(),
    metric_avg_hit_5(),
    metric_avg_hit_10(),
    metric_avg_cutoff_f1(),
    metric_avg_cutoff_precision(),
    metric_avg_cutoff_recall(),
    metric_p95_latency(),
])
def ranking_scorer() -> Scorer:
    """NDCG, Hit@K, and cutoff F1/P/R for def-level ranking evaluation."""

    async def score(state: TaskState, target: Target) -> Score:
        meta = state.metadata
        ranked_candidate_keys = state.store.get("ranked_candidate_keys", [])
        predicted_n = state.store.get("predicted_n", 0)
        gt_edited = meta.get("gt_edited", meta.get("gt_edited_keys", []))
        gt_read_necessary = meta.get("gt_read_necessary", meta.get("gt_read_keys", []))
        query_type = meta.get("query_type", "UNKNOWN")
        latency_sec = state.store.get("latency_sec", 0.0)

        edited_set = set(gt_edited)
        read_set = set(gt_read_necessary)
        all_gt = edited_set | read_set

        ideal_relevances = [2.0] * len(edited_set) + [1.0] * len(read_set)

        actual_relevances = []
        for key in ranked_candidate_keys:
            if key in edited_set:
                actual_relevances.append(2.0)
            elif key in read_set:
                actual_relevances.append(1.0)
            else:
                actual_relevances.append(0.0)

        ndcg_5 = _ndcg(actual_relevances, ideal_relevances, k=5)
        ndcg_10 = _ndcg(actual_relevances, ideal_relevances, k=10)
        ndcg_20 = _ndcg(actual_relevances, ideal_relevances, k=20)
        ndcg_full = _ndcg(actual_relevances, ideal_relevances)

        top_5 = set(ranked_candidate_keys[:5])
        top_10 = set(ranked_candidate_keys[:10])
        hit_5 = 1.0 if top_5 & edited_set else 0.0
        hit_10 = 1.0 if top_10 & edited_set else 0.0

        returned = set(ranked_candidate_keys[:predicted_n])
        cutoff_prf = _prf(returned, all_gt)

        return Score(
            value=round(ndcg_10, 4),
            answer=str(ranked_candidate_keys[:10]),
            metadata={
                "ndcg_5": round(ndcg_5, 4),
                "ndcg_10": round(ndcg_10, 4),
                "ndcg_20": round(ndcg_20, 4),
                "ndcg_full": round(ndcg_full, 4),
                "hit_5": hit_5,
                "hit_10": hit_10,
                "cutoff_precision": cutoff_prf["precision"],
                "cutoff_recall": cutoff_prf["recall"],
                "cutoff_f1": cutoff_prf["f1"],
                "predicted_n": predicted_n,
                "latency_sec": latency_sec,
                "gt_edited_count": len(edited_set),
                "gt_read_count": len(read_set),
                "query_type": query_type,
            },
        )

    return score

