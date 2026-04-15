"""Gate scorer — accuracy and confusion matrix.

Inspect AI scorer for the gate classifier evaluation.

Evaluates the gate classifier's ability to correctly route queries
as OK / UNSAT / BROAD / AMBIG.
"""

from __future__ import annotations

from inspect_ai.scorer import (
    Metric,
    Score,
    Scorer,
    Target,
    metric,
    scorer,
)
from inspect_ai.solver import TaskState

_GATE_LABELS = ("OK", "UNSAT", "BROAD", "AMBIG")


# ── Custom metric for aggregation ────────────────────────────────────────


@metric
def metric_gate_accuracy() -> Metric:
    def compute(scores: list[Score]) -> float:
        if not scores:
            return 0.0
        correct = sum(1.0 for s in scores if s.value == 1.0)
        return round(correct / len(scores), 4)
    return compute


# ── Scorer ────────────────────────────────────────────────────────────────


@scorer(metrics=[metric_gate_accuracy()])
def gate_scorer() -> Scorer:
    """Gate classification accuracy and confusion matrix."""

    async def score(state: TaskState, target: Target) -> Score:
        meta = state.metadata
        predicted_gate = state.store.get("predicted_gate", "")
        gt_gate = meta.get("label_gate", "")
        query_type = meta.get("query_type", "UNKNOWN")

        correct = 1.0 if predicted_gate == gt_gate else 0.0

        return Score(
            value=correct,
            answer=predicted_gate,
            metadata={
                "correct": correct,
                "predicted": predicted_gate,
                "actual": gt_gate,
                "query_type": query_type,
            },
        )

    return score

