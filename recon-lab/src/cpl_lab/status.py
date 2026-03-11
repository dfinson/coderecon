"""Pipeline status dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from cpl_lab.clone import REPO_SETS


def run_status(config: dict[str, Any], verbose: bool = False) -> None:
    """Show pipeline state across all stages."""
    clones_dir: Path = config["clones_dir"]
    data_dir: Path = config["data_dir"]
    models_dir: Path = config["models_dir"]

    click.echo(f"Workspace: {config['workspace']}")
    click.echo()

    # ── Clones ───────────────────────────────────────────────────
    click.echo("=== Clones ===")
    for set_name, manifest in REPO_SETS.items():
        set_dir = clones_dir / set_name
        expected = len(manifest)
        cloned = sum(1 for d in set_dir.iterdir() if (d / ".git").is_dir()) if set_dir.is_dir() else 0
        indexed = sum(1 for d in set_dir.iterdir() if (d / ".codeplane").is_dir()) if set_dir.is_dir() else 0
        click.echo(f"  {set_name:14s}  cloned: {cloned:2d}/{expected:2d}  indexed: {indexed:2d}/{expected:2d}")

    # ── Ground Truth ─────────────────────────────────────────────
    click.echo("\n=== Ground Truth ===")
    gt_repos = 0
    gt_tasks = 0
    if data_dir.is_dir():
        for rd in sorted(data_dir.iterdir()):
            if not rd.is_dir() or rd.name == "merged":
                continue
            gt_dir = rd / "ground_truth"
            if gt_dir.is_dir():
                tasks = list(gt_dir.glob("*.json"))
                if tasks:
                    gt_repos += 1
                    gt_tasks += len(tasks)
                    if verbose:
                        click.echo(f"  {rd.name}: {len(tasks)} tasks")
    click.echo(f"  Repos with GT: {gt_repos}  Total tasks: {gt_tasks}")

    # ── Signals ──────────────────────────────────────────────────
    click.echo("\n=== Signals ===")
    sig_repos = 0
    if data_dir.is_dir():
        for rd in sorted(data_dir.iterdir()):
            if not rd.is_dir() or rd.name == "merged":
                continue
            if (rd / "signals" / "candidates_rank.jsonl").exists():
                sig_repos += 1
    click.echo(f"  Repos with signals: {sig_repos}")

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
