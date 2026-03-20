"""Pipeline status dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from cpl_lab.collector import iter_task_json_files
from cpl_lab.data_manifest import iter_repo_data_dirs, repo_set_for_dir

PIPELINE_STEPS = ["swebench", "collect", "merge", "train"]


def _pipeline_position(
    data_dir: Path,
    models_dir: Path,
) -> tuple[str, str]:
    """Return (current_step, detail) for the overall pipeline."""
    imported_instances = 0
    for repo_dir in iter_repo_data_dirs(data_dir):
        gt_dir = repo_dir / "ground_truth"
        if (gt_dir / "queries.jsonl").exists() or iter_task_json_files(gt_dir):
            imported_instances += 1
    if imported_instances == 0:
        return "swebench", "no SWE-bench ground truth yet — run 'cpl-lab swebench'"

    # Check signals
    sig_repos = 0
    for repo_dir in iter_repo_data_dirs(data_dir):
        if (repo_dir / "signals" / "candidates_rank.parquet").exists():
            sig_repos += 1
    if sig_repos < imported_instances:
        return "collect", f"{sig_repos}/{imported_instances} instances have signals"

    # Check merge
    merged = data_dir / "merged"
    if not merged.is_dir() or not list(merged.glob("*.parquet")):
        return "merge", "no merged parquets yet"

    # Check training
    if not models_dir.is_dir() or not list(models_dir.glob("*.lgbm")):
        return "train", "no trained models yet"
    trained = sorted(m.stem for m in models_dir.glob("*.lgbm"))
    expected = {"ranker", "cutoff", "gate"}
    missing = expected - set(trained)
    if missing:
        return "train", f"missing models: {', '.join(sorted(missing))}"

    return "done", "pipeline complete — ready for eval"


def run_status(config: dict[str, Any], verbose: bool = False) -> None:
    """Show pipeline state across all stages."""
    data_dir: Path = config["data_dir"]
    models_dir: Path = config["models_dir"]

    click.echo(f"Workspace: {config['workspace']}")
    click.echo()

    # ── Pipeline Position ────────────────────────────────────────
    step, detail = _pipeline_position(data_dir, models_dir)
    step_idx = PIPELINE_STEPS.index(step) if step in PIPELINE_STEPS else len(PIPELINE_STEPS)

    click.echo("=== Pipeline Position ===")
    for i, s in enumerate(PIPELINE_STEPS):
        if i < step_idx:
            click.echo(f"  ✓ {s}")
        elif i == step_idx:
            click.echo(f"  ▸ {s}  ← {detail}")
        else:
            click.echo(f"    {s}")
    if step == "done":
        click.echo(f"  ✓ {detail}")

    # ── Ground Truth ─────────────────────────────────────────────
    click.echo("\n=== Ground Truth ===")
    gt_repos = 0
    gt_tasks = 0
    by_set: dict[str, int] = {}
    for repo_dir in iter_repo_data_dirs(data_dir):
        gt_dir = repo_dir / "ground_truth"
        if not gt_dir.is_dir():
            continue
        tasks = iter_task_json_files(gt_dir)
        if not tasks:
            continue
        gt_repos += 1
        gt_tasks += len(tasks)
        repo_set = repo_set_for_dir(repo_dir) or "unknown"
        by_set[repo_set] = by_set.get(repo_set, 0) + 1
        if verbose:
            click.echo(f"  {repo_dir.name}: {len(tasks)} tasks")
    click.echo(f"  Instances with GT: {gt_repos}  Total tasks: {gt_tasks}")
    if by_set:
        click.echo("  By set: " + ", ".join(f"{name}={count}" for name, count in sorted(by_set.items())))

    # ── Signals ──────────────────────────────────────────────────
    click.echo("\n=== Signals ===")
    sig_repos = 0
    for repo_dir in iter_repo_data_dirs(data_dir):
        if (repo_dir / "signals" / "candidates_rank.parquet").exists():
            sig_repos += 1
    click.echo(f"  Instances with signals: {sig_repos}")

    # ── Merged ───────────────────────────────────────────────────
    click.echo("\n=== Merged Data ===")
    merged = data_dir / "merged"
    if merged.is_dir():
        for f in sorted(merged.glob("*.parquet")):
            size_mb = f.stat().st_size / (1024 * 1024)
            click.echo(f"  {f.name}: {size_mb:.1f} MB")
    else:
        click.echo("  (not yet merged)")

    # ── Models ───────────────────────────────────────────────────
    click.echo("\n=== Models ===")
    if models_dir.is_dir():
        models = list(models_dir.glob("*.lgbm"))
        if models:
            for m in sorted(models):
                size_kb = m.stat().st_size / 1024
                click.echo(f"  {m.name}: {size_kb:.0f} KB")
        else:
            click.echo("  (no trained models)")
    else:
        click.echo("  (models directory not found)")
