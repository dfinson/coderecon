"""Inspect AI @task definitions for GT discovery experiment."""

from __future__ import annotations

from inspect_ai import Task, task

from recon_lab.eval.gt_discovery.dataset import gt_discovery_dataset
from recon_lab.eval.gt_discovery.scorer import gt_discovery_scorer
from recon_lab.eval.gt_discovery.solver import gt_discovery_solver


@task
def gt_discovery(
    data_dir: str = "~/.recon/recon-lab/data",
    clone_dir: str = "~/.recon/recon-lab/clones/instances",
    n_modification: int = 40,
    n_mixed: int = 40,
    n_creation_heavy: int = 40,
    max_turns: int = 15,
    seed: int = 42,
) -> Task:
    """GT discovery experiment: agent-driven exploration of code context.

    Runs an LLM agent with CodeRecon tools to explore repos and find
    relevant definitions. Compares agent traces against known GT to
    identify structural patterns for GT expansion rules.

    Args:
        data_dir: Path to recon-lab data directory.
        clone_dir: Path to cloned instances.
        n_modification: Samples from modification-only bucket.
        n_mixed: Samples from mixed (some new files) bucket.
        n_creation_heavy: Samples from creation-heavy bucket.
        max_turns: Maximum agent turns per sample.
        seed: Random seed for stratified sampling.
    """
    return Task(
        dataset=gt_discovery_dataset(
            data_dir=data_dir,
            n_modification=n_modification,
            n_mixed=n_mixed,
            n_creation_heavy=n_creation_heavy,
            seed=seed,
        ),
        solver=gt_discovery_solver(
            clone_dir=clone_dir,
            max_turns=max_turns,
        ),
        scorer=gt_discovery_scorer(),
        max_messages=max_turns * 2 + 5,
    )
