"""Ranking scorer — NDCG, Hit@K, Cutoff F1/Precision/Recall.

Inspect AI scorer for the ranking pipeline evaluation.

Evaluates the ranker's ranked list quality and the cutoff model's
set-selection quality against def-level ground truth.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

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
def metric_avg_f1_10() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "f1_10")
    return compute


@metric
def metric_avg_precision_10() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "precision_10")
    return compute


@metric
def metric_avg_recall_10() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "recall_10")
    return compute


@metric
def metric_avg_f1_20() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "f1_20")
    return compute


@metric
def metric_avg_precision_20() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "precision_20")
    return compute


@metric
def metric_avg_recall_20() -> Metric:
    def compute(scores: list[Score]) -> float:
        return _avg_metadata_field(scores, "recall_20")
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
    metric_avg_f1_10(),
    metric_avg_precision_10(),
    metric_avg_recall_10(),
    metric_avg_f1_20(),
    metric_avg_precision_20(),
    metric_avg_recall_20(),
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
        top_20 = set(ranked_candidate_keys[:20])
        hit_5 = 1.0 if top_5 & edited_set else 0.0
        hit_10 = 1.0 if top_10 & edited_set else 0.0

        prf_10 = _prf(top_10, all_gt)
        prf_20 = _prf(top_20, all_gt)

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
                "precision_10": prf_10["precision"],
                "recall_10": prf_10["recall"],
                "f1_10": prf_10["f1"],
                "precision_20": prf_20["precision"],
                "recall_20": prf_20["recall"],
                "f1_20": prf_20["f1"],
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


# ── Diagnostic scorer ─────────────────────────────────────────────────────
# Extends the base ranking scorer with funnel analysis, per-harvester
# recall, Recall/Precision/F1@K, MRR, per-list solo NDCG, LOO ablation.

_HARVESTER_NAMES = ["term_match", "explicit", "graph", "import", "splade", "coverage"]
_K_VALUES = [1, 5, 10, 20, 50, 100]
_ALL_LIST_NAMES = [
    "term_match", "explicit", "graph", "import", "splade",
    "shares_file", "coverage", "retriever_agreement", "hub_score",
    "callee_of_seed", "imported_by_seed",
]


def _make_avg_metric(field: str):
    """Create a Metric that averages a metadata field across scores."""
    def factory() -> Metric:
        def compute(scores: list[Score]) -> float:
            return _avg_metadata_field(scores, field)
        return compute
    factory.__name__ = f"metric_avg_{field}"
    factory.__qualname__ = f"metric_avg_{field}"
    return metric(factory)()


_DIAGNOSTIC_METRICS = [
    # Funnel
    _make_avg_metric("pool_size"),
    _make_avg_metric("pool_recall"),
    _make_avg_metric("post_prune_recall"),
    _make_avg_metric("prune_loss"),
    # Per-harvester recall
    *[_make_avg_metric(f"recall_{h}") for h in _HARVESTER_NAMES],
    # Recall / Precision / F1 at K (relative to total GT)
    *[_make_avg_metric(f"recall_{k}") for k in _K_VALUES],
    *[_make_avg_metric(f"precision_{k}") for k in _K_VALUES],
    *[_make_avg_metric(f"f1_{k}") for k in _K_VALUES],
    # Rank-based
    _make_avg_metric("mrr"),
    _make_avg_metric("gt_median_rank"),
    # NDCG / Hit
    _make_avg_metric("ndcg_5"),
    _make_avg_metric("ndcg_10"),
    _make_avg_metric("ndcg_20"),
    _make_avg_metric("hit_5"),
    _make_avg_metric("hit_10"),
    # Cutoff
    _make_avg_metric("cutoff_f1"),
    _make_avg_metric("cutoff_precision"),
    _make_avg_metric("cutoff_recall"),
    # Solo NDCG@20 per rank list
    *[_make_avg_metric(f"solo_ndcg_20__{ln}") for ln in _ALL_LIST_NAMES],
    # LOO delta per rank list
    *[_make_avg_metric(f"loo_delta__{ln}") for ln in _ALL_LIST_NAMES],
    # Latency
    metric_p95_latency(),
]


@scorer(metrics=_DIAGNOSTIC_METRICS)
def diagnostic_ranking_scorer() -> Scorer:
    """Full diagnostic scorer: funnel, attribution, Recall/P/F1@K, solo/LOO ablation."""

    async def score(state: TaskState, target: Target) -> Score:
        meta = state.metadata
        store = state.store

        # Read diagnostic data from solver
        ranked_candidate_keys: list[str] = store.get("ranked_candidate_keys", [])
        predicted_n: int = store.get("predicted_n", 0)
        pool_keys: list[str] = store.get("pool_candidate_keys", [])
        per_harvester_keys: dict[str, list[str]] = store.get("per_harvester_keys", {})
        post_prune_keys: list[str] = store.get("post_prune_keys", [])
        per_list_ordered_keys: dict[str, list[str]] = store.get("per_list_ordered_keys", {})
        pool_size: int = store.get("pool_size", 0)
        post_prune_pool_size: int = store.get("post_prune_pool_size", 0)
        latency_sec: float = store.get("latency_sec", 0.0)
        repo_id: str = store.get("repo_id", "")

        # Ground truth
        gt_edited = meta.get("gt_edited", meta.get("gt_edited_keys", []))
        gt_read_necessary = meta.get("gt_read_necessary", meta.get("gt_read_keys", []))
        query_type = meta.get("query_type", "UNKNOWN")

        edited_set = set(gt_edited)
        read_set = set(gt_read_necessary)
        all_gt = edited_set | read_set
        gt_count = len(all_gt)

        # ── Funnel metrics ────────────────────────────────────────────
        pool_set = set(pool_keys)
        post_prune_set = set(post_prune_keys)

        pool_recall = len(all_gt & pool_set) / gt_count if gt_count else 0.0
        post_prune_recall = len(all_gt & post_prune_set) / gt_count if gt_count else 0.0
        prune_loss = pool_recall - post_prune_recall

        # Per-harvester recall
        harvester_recalls: dict[str, float] = {}
        for h_name in _HARVESTER_NAMES:
            h_set = set(per_harvester_keys.get(h_name, []))
            harvester_recalls[h_name] = (
                len(all_gt & h_set) / gt_count if gt_count else 0.0
            )

        # ── NDCG ──────────────────────────────────────────────────────
        ideal_relevances = [2.0] * len(edited_set) + [1.0] * len(read_set)

        actual_relevances: list[float] = []
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

        # Hit@K
        hit_5 = 1.0 if set(ranked_candidate_keys[:5]) & edited_set else 0.0
        hit_10 = 1.0 if set(ranked_candidate_keys[:10]) & edited_set else 0.0

        # Cutoff P/R/F1
        returned = set(ranked_candidate_keys[:predicted_n])
        cutoff_prf = _prf(returned, all_gt)

        # ── Recall / Precision / F1 at K (relative to total GT) ──────
        rpf_at_k: dict[str, float] = {}
        for k_val in _K_VALUES:
            top_k = set(ranked_candidate_keys[:k_val])
            tp = len(top_k & all_gt)
            p = tp / min(k_val, len(ranked_candidate_keys)) if ranked_candidate_keys else 0.0
            r = tp / gt_count if gt_count else 0.0
            f = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
            rpf_at_k[f"recall_{k_val}"] = round(r, 4)
            rpf_at_k[f"precision_{k_val}"] = round(p, 4)
            rpf_at_k[f"f1_{k_val}"] = round(f, 4)

        # ── MRR ───────────────────────────────────────────────────────
        mrr = 0.0
        for rank, key in enumerate(ranked_candidate_keys, 1):
            if key in all_gt:
                mrr = 1.0 / rank
                break

        # ── GT median rank ────────────────────────────────────────────
        ranked_pos = {key: pos for pos, key in enumerate(ranked_candidate_keys, 1)}
        gt_ranks = sorted(
            ranked_pos.get(gt_key, pool_size + 1) for gt_key in all_gt
        )
        gt_median_rank = (
            gt_ranks[len(gt_ranks) // 2] if gt_ranks else 0.0
        )

        # ── Solo NDCG@20 per rank list ────────────────────────────────
        solo_ndcg: dict[str, float] = {}
        for ln in _ALL_LIST_NAMES:
            list_keys = per_list_ordered_keys.get(ln, [])
            if not list_keys:
                solo_ndcg[f"solo_ndcg_20__{ln}"] = 0.0
                continue
            solo_rels: list[float] = []
            for key in list_keys:
                if key in edited_set:
                    solo_rels.append(2.0)
                elif key in read_set:
                    solo_rels.append(1.0)
                else:
                    solo_rels.append(0.0)
            solo_ndcg[f"solo_ndcg_20__{ln}"] = round(
                _ndcg(solo_rels, ideal_relevances, k=20), 4,
            )

        # ── LOO delta: NDCG@20(all) - NDCG@20(all minus list_i) ──────
        loo_delta: dict[str, float] = {}
        _RRF_K = 60
        for target_ln in _ALL_LIST_NAMES:
            if target_ln not in per_list_ordered_keys:
                loo_delta[f"loo_delta__{target_ln}"] = 0.0
                continue
            # Recompute RRF without target list
            loo_scores: dict[str, float] = {}
            for ln, list_keys in per_list_ordered_keys.items():
                if ln == target_ln:
                    continue
                for rank_pos, key in enumerate(list_keys, 1):
                    loo_scores[key] = loo_scores.get(key, 0.0) + 1.0 / (_RRF_K + rank_pos)
            loo_ranked = sorted(loo_scores, key=lambda k: -loo_scores[k])
            loo_rels: list[float] = []
            for key in loo_ranked:
                if key in edited_set:
                    loo_rels.append(2.0)
                elif key in read_set:
                    loo_rels.append(1.0)
                else:
                    loo_rels.append(0.0)
            loo_ndcg = _ndcg(loo_rels, ideal_relevances, k=20)
            loo_delta[f"loo_delta__{target_ln}"] = round(ndcg_20 - loo_ndcg, 4)

        # ── Assemble metadata ─────────────────────────────────────────
        result_meta: dict[str, Any] = {
            # Funnel
            "pool_size": pool_size,
            "pool_recall": round(pool_recall, 4),
            "post_prune_pool_size": post_prune_pool_size,
            "post_prune_recall": round(post_prune_recall, 4),
            "prune_loss": round(prune_loss, 4),
            # Per-harvester recall
            **{f"recall_{h}": round(v, 4) for h, v in harvester_recalls.items()},
            # Recall / Precision / F1 at K
            **rpf_at_k,
            # Rank-based
            "mrr": round(mrr, 4),
            "gt_median_rank": gt_median_rank,
            # NDCG / Hit
            "ndcg_5": round(ndcg_5, 4),
            "ndcg_10": round(ndcg_10, 4),
            "ndcg_20": round(ndcg_20, 4),
            "ndcg_full": round(ndcg_full, 4),
            "hit_5": hit_5,
            "hit_10": hit_10,
            # Cutoff
            "cutoff_precision": cutoff_prf["precision"],
            "cutoff_recall": cutoff_prf["recall"],
            "cutoff_f1": cutoff_prf["f1"],
            "predicted_n": predicted_n,
            # Solo NDCG
            **solo_ndcg,
            # LOO delta
            **loo_delta,
            # Identity
            "gt_edited_count": len(edited_set),
            "gt_read_count": len(read_set),
            "query_type": query_type,
            "repo_id": repo_id,
            "latency_sec": latency_sec,
        }

        return Score(
            value=round(ndcg_10, 4),
            answer=str(ranked_candidate_keys[:10]),
            metadata=result_meta,
        )

    return score

