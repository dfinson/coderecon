"""Inspect AI task definitions for CodeRecon evaluation.

Each @task function composes a dataset, solver (pipeline), and scorer.
Run via:
    inspect eval cpl_lab/eval/tasks.py@ranking_baseline
    inspect eval cpl_lab/eval/tasks.py@ranking_trained
    inspect eval cpl_lab/eval/tasks.py@ranking_micro

Or via the CLI:
    recon-lab eval                     # runs ranking_baseline + ranking_trained
    recon-lab micro-eval               # offline sanity check from merged parquet
"""

from __future__ import annotations

from inspect_ai import Task, task

from cpl_lab.eval.datasets.eval_gt import eval_gt_dataset
from cpl_lab.eval.metrics.gate import gate_scorer
from cpl_lab.eval.metrics.ranking import diagnostic_ranking_scorer, ranking_scorer
from cpl_lab.eval.models.offline_ranking import (
    offline_ce_only_solver,
    offline_ranking_solver,
    offline_rrf_solver,
)
from cpl_lab.eval.models.ranking import diagnostic_ranking_solver, ranking_solver

# ── Ranking pipeline tasks ────────────────────────────────────────────────


@task
def ranking_baseline(
    data_dir: str = "~/.recon/recon-lab/data",
    clone_dir: str = "~/.recon/recon-lab/clones/instances",
    models_dir: str = "~/.recon/recon-lab/models",
) -> Task:
    """Baseline ranking: RRF heuristic fallback, always-OK gate, fixed cutoff."""
    return Task(
        dataset=eval_gt_dataset(data_dir=data_dir),
        solver=ranking_solver(
            clone_dir=clone_dir,
            mode="baseline",
            models_dir=models_dir,
        ),
        scorer=[ranking_scorer(), gate_scorer()],
        max_messages=1,
    )


@task
def ranking_trained(
    data_dir: str = "~/.recon/recon-lab/data",
    clone_dir: str = "~/.recon/recon-lab/clones/instances",
    models_dir: str = "~/.recon/recon-lab/models",
) -> Task:
    """Trained ranking: LightGBM gate + ranker + cutoff."""
    return Task(
        dataset=eval_gt_dataset(data_dir=data_dir),
        solver=ranking_solver(
            clone_dir=clone_dir,
            mode="ranking",
            models_dir=models_dir,
        ),
        scorer=[ranking_scorer(), gate_scorer()],
        max_messages=1,
    )


@task
def ranking_diagnostic(
    data_dir: str = "~/.recon/recon-lab/data",
    clone_dir: str = "~/.recon/recon-lab/clones/instances",
    models_dir: str = "~/.recon/recon-lab/models",
) -> Task:
    """Diagnostic RRF evaluation: funnel analysis, per-list attribution, ablation."""
    return Task(
        dataset=eval_gt_dataset(data_dir=data_dir),
        solver=diagnostic_ranking_solver(
            clone_dir=clone_dir,
            models_dir=models_dir,
        ),
        scorer=[diagnostic_ranking_scorer(), gate_scorer()],
        max_messages=1,
    )


# ── Micro-eval: offline sanity check from merged parquet ──────────────────


@task
def ranking_micro(
    data_dir: str = "~/.recon/recon-lab/data",
    models_dir: str = "~/.recon/recon-lab/models",
) -> Task:
    """Offline micro-eval: score pre-collected candidates, no daemon needed."""
    merged_dir = str(data_dir.rstrip("/") + "/merged") if isinstance(data_dir, str) else str(data_dir / "merged")
    return Task(
        dataset=eval_gt_dataset(data_dir=data_dir),
        solver=offline_ranking_solver(
            merged_dir=merged_dir,
            models_dir=models_dir,
        ),
        scorer=[ranking_scorer(), gate_scorer()],
        max_messages=1,
    )


@task
def ranking_micro_rrf(
    data_dir: str = "~/.recon/recon-lab/data",
) -> Task:
    """Baseline: rank by RRF score only, no learned models."""
    merged_dir = str(data_dir.rstrip("/") + "/merged") if isinstance(data_dir, str) else str(data_dir / "merged")
    return Task(
        dataset=eval_gt_dataset(data_dir=data_dir),
        solver=offline_rrf_solver(merged_dir=merged_dir),
        scorer=[ranking_scorer(), gate_scorer()],
        max_messages=1,
    )


@task
def ranking_micro_ce_only(
    data_dir: str = "~/.recon/recon-lab/data",
) -> Task:
    """Baseline: rank by cross-encoder (TinyBERT) score only."""
    merged_dir = str(data_dir.rstrip("/") + "/merged") if isinstance(data_dir, str) else str(data_dir / "merged")
    return Task(
        dataset=eval_gt_dataset(data_dir=data_dir),
        solver=offline_ce_only_solver(merged_dir=merged_dir),
        scorer=[ranking_scorer(), gate_scorer()],
        max_messages=1,
    )
