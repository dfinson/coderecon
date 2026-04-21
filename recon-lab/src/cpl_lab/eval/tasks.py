"""Inspect AI task definitions for CodeRecon evaluation.

Each @task function composes a dataset, solver (pipeline), and scorer.
Run via:
    inspect eval cpl_lab/eval/tasks.py@ranking_baseline
    inspect eval cpl_lab/eval/tasks.py@ranking_trained

Or via the CLI:
    recon-lab eval                     # runs ranking_baseline + ranking_trained
"""

from __future__ import annotations

from inspect_ai import Task, task

from cpl_lab.eval.datasets.eval_gt import eval_gt_dataset
from cpl_lab.eval.metrics.gate import gate_scorer
from cpl_lab.eval.metrics.ranking import diagnostic_ranking_scorer, ranking_scorer
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
            variant="structural",
        ),
        scorer=[ranking_scorer(), gate_scorer()],
        max_messages=1,
    )


@task
def ranking_trained(
    data_dir: str = "~/.recon/recon-lab/data",
    clone_dir: str = "~/.recon/recon-lab/clones/instances",
    models_dir: str = "~/.recon/recon-lab/models",
    variant: str = "structural",
) -> Task:
    """Trained ranking: LightGBM gate + ranker + cutoff."""
    return Task(
        dataset=eval_gt_dataset(data_dir=data_dir),
        solver=ranking_solver(
            clone_dir=clone_dir,
            mode="ranking",
            models_dir=models_dir,
            variant=variant,
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
            variant="structural",
        ),
        scorer=[diagnostic_ranking_scorer(), gate_scorer()],
        max_messages=1,
    )
