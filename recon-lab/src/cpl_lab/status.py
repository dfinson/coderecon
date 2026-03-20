"""Pipeline status dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from cpl_lab.collector import iter_task_json_files
from cpl_lab.clone import REPO_SETS
PIPELINE_STEPS = ["clone", "index", "mine", "collect", "merge", "train"]


def _pipeline_position(
    clones_dir: Path,
    data_dir: Path,
    models_dir: Path,
) -> tuple[str, str]:
    """Return (current_step, detail) for the overall pipeline."""
    # Check clones
    total_expected = sum(len(m) for m in REPO_SETS.values())
    total_cloned = 0
    for set_name, manifest in REPO_SETS.items():
        sd = clones_dir / set_name
        if sd.is_dir():
            total_cloned += sum(1 for d in sd.iterdir() if (d / ".git").is_dir())
    if total_cloned < total_expected:
        return "clone", f"{total_cloned}/{total_expected} repos cloned"

    # Check indexing
    total_indexed = 0
    for set_name in REPO_SETS:
        sd = clones_dir / set_name
        if sd.is_dir():
            total_indexed += sum(1 for d in sd.iterdir() if (d / ".recon").is_dir())
    if total_indexed < total_expected:
        return "index", f"{total_indexed}/{total_expected} repos indexed"

    # total_expected is already computed above from REPO_SETS manifest.
    mined_repos = 0
    if data_dir.is_dir():
        for repo_dir in data_dir.iterdir():
            if not repo_dir.is_dir() or repo_dir.name == "merged":
                continue
            gt_dir = repo_dir / "ground_truth"
            if (gt_dir / "queries.jsonl").exists() or iter_task_json_files(gt_dir):
                mined_repos += 1
    if mined_repos == 0:
        return "mine", "no mined ground truth yet — run 'cpl-lab mine'"
    if mined_repos < total_expected:
        return "mine", f"{mined_repos}/{total_expected} repos have mined ground truth"

    # Check signals
    sig_repos = 0
    if data_dir.is_dir():
        for rd in data_dir.iterdir():
            if rd.is_dir() and rd.name != "merged" and (rd / "signals" / "candidates_rank.parquet").exists():
                sig_repos += 1
    gt_repos = mined_repos
    if sig_repos < gt_repos:
        return "collect", f"{sig_repos}/{gt_repos} repos have signals"

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
    clones_dir: Path = config["clones_dir"]
    data_dir: Path = config["data_dir"]
    models_dir: Path = config["models_dir"]

    click.echo(f"Workspace: {config['workspace']}")
    click.echo()

    # ── Pipeline Position ────────────────────────────────────────
    step, detail = _pipeline_position(clones_dir, data_dir, models_dir)
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

    # ── Clones ───────────────────────────────────────────────────
    click.echo("\n=== Clones ===")
    for set_name, manifest in REPO_SETS.items():
        set_dir = clones_dir / set_name
        expected = len(manifest)
        cloned = sum(1 for d in set_dir.iterdir() if (d / ".git").is_dir()) if set_dir.is_dir() else 0
        indexed = sum(1 for d in set_dir.iterdir() if (d / ".recon").is_dir()) if set_dir.is_dir() else 0
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
                tasks = iter_task_json_files(gt_dir)
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
            if (rd / "signals" / "candidates_rank.parquet").exists():
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
