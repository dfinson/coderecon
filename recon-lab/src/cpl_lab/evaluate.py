"""Evaluate models via EVEE — CLI adapter for benchmarking integration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from cpl_lab.config import LAB_ROOT


def run_eval(
    config: dict[str, Any],
    experiment: str = "recon_ranking.yaml",
    model_dir: Path | None = None,
    verbose: bool = False,
) -> None:
    """Run EVEE evaluation against trained models.

    Delegates to the benchmarking/ project's run.py, which handles
    experiment loading, model execution, and metric computation.
    """
    # Locate benchmarking project (sibling to recon-lab in the repo)
    repo_root = LAB_ROOT.parent
    bench_dir = repo_root / "benchmarking"

    if not bench_dir.is_dir():
        raise click.ClickException(
            f"Benchmarking directory not found at {bench_dir}"
        )

    run_script = bench_dir / "run.py"
    if not run_script.is_file():
        raise click.ClickException(f"run.py not found at {run_script}")

    # Resolve experiment file
    exp_path = bench_dir / "experiments" / experiment
    if not exp_path.is_file():
        raise click.ClickException(f"Experiment not found: {exp_path}")

    model_dir = model_dir or config["models_dir"]

    cmd = [
        sys.executable, str(run_script),
        "--experiment", str(exp_path),
        "--model-dir", str(model_dir),
    ]
    if verbose:
        cmd.append("--verbose")

    click.echo(f"Running EVEE evaluation: {experiment}")
    click.echo(f"  models: {model_dir}")
    click.echo(f"  experiment: {exp_path}")

    result = subprocess.run(cmd, cwd=bench_dir)
    if result.returncode != 0:
        raise click.ClickException(f"Evaluation failed with exit code {result.returncode}")
