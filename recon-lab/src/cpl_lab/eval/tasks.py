"""Inspect AI task definitions for CodeRecon evaluation.

Each @task function composes a dataset, solver (pipeline), and scorer.
Run via:
    inspect eval cpl_lab/eval/tasks.py@ranking_baseline
    inspect eval cpl_lab/eval/tasks.py@ranking_trained
    inspect eval cpl_lab/eval/tasks.py@llm_reranker_baseline
    inspect eval cpl_lab/eval/tasks.py@llm_reranker_azure
    inspect eval cpl_lab/eval/tasks.py@llm_reranker_local

Or via the CLI:
    recon-lab eval                     # runs ranking_baseline + ranking_trained
    recon-lab eval --experiment llm    # runs llm reranker tasks
"""

from __future__ import annotations

from inspect_ai import Task, task

from cpl_lab.eval.datasets.eval_gt import eval_gt_dataset
from cpl_lab.eval.datasets.scaffold_rank import scaffold_rank_dataset
from cpl_lab.eval.metrics.gate import gate_scorer
from cpl_lab.eval.metrics.ranking import ranking_scorer
from cpl_lab.eval.models.llm_reranker import llm_reranker
from cpl_lab.eval.models.ranking import ranking_solver

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


# ── LLM reranker tasks ───────────────────────────────────────────────────


@task
def llm_reranker_baseline(
    data_file: str = "~/.recon/recon-lab/data/scaffold_rerank_data.jsonl",
) -> Task:
    """Baseline: retriever-agreement order, no LLM (passthrough)."""
    return Task(
        dataset=scaffold_rank_dataset(data_file=data_file),
        solver=llm_reranker(backend="passthrough", top_n=20, predicted_n=10),
        scorer=ranking_scorer(),
        max_messages=1,
    )


@task
def llm_reranker_azure(
    data_file: str = "~/.recon/recon-lab/data/scaffold_rerank_data.jsonl",
    llm_model: str = "gpt-4.1-mini",
    top_n: int = 20,
    predicted_n: int = 10,
) -> Task:
    """GPT-4.1-mini via Azure OpenAI (quality ceiling)."""
    return Task(
        dataset=scaffold_rank_dataset(data_file=data_file),
        solver=llm_reranker(
            backend="azure",
            llm_model=llm_model,
            top_n=top_n,
            predicted_n=predicted_n,
            max_tokens=512,
            timeout=60,
        ),
        scorer=ranking_scorer(),
        max_messages=1,
    )


@task
def llm_reranker_local(
    data_file: str = "~/.recon/recon-lab/data/scaffold_rerank_data.jsonl",
    llm_model: str = "qwen2.5-coder:3b",
    local_endpoint: str = "http://localhost:11434/v1",
    top_n: int = 20,
    predicted_n: int = 10,
) -> Task:
    """Qwen2.5-Coder-3B via local ollama (candidate)."""
    return Task(
        dataset=scaffold_rank_dataset(data_file=data_file),
        solver=llm_reranker(
            backend="local",
            llm_model=llm_model,
            local_endpoint=local_endpoint,
            top_n=top_n,
            predicted_n=predicted_n,
            max_tokens=512,
            timeout=120,
        ),
        scorer=ranking_scorer(),
        max_messages=1,
    )
