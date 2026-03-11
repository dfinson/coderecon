"""Train models — CLI adapter wrapping train_all and individual trainers."""

from __future__ import annotations

from pathlib import Path

import click


def run_train(
    data_dir: Path,
    output_dir: Path,
    model: str = "all",
    skip_merge: bool = False,
    verbose: bool = False,
) -> None:
    """Train ranking models."""
    output_dir.mkdir(parents=True, exist_ok=True)
    merged_dir = data_dir / "merged"

    # Validate pre-merged ground truth exists
    for required in ("queries.parquet", "touched_objects.parquet"):
        if not (merged_dir / required).exists():
            raise click.ClickException(
                f"Missing {merged_dir / required} — run `cpl-lab merge --what gt` first"
            )

    if model == "all":
        from cpl_lab.train_all import train_all

        train_all(data_dir, output_dir, skip_merge=skip_merge)
    elif model == "gate":
        from cpl_lab.train_gate import train_gate

        click.echo("=== Training Gate ===")
        summary = train_gate(merged_dir=merged_dir, output_path=output_dir / "gate.lgbm")
        click.echo(f"  {summary['total_queries']} queries")
    elif model == "ranker":
        from cpl_lab.train_ranker import train_ranker

        click.echo("=== Training Ranker ===")
        summary = train_ranker(merged_dir=merged_dir, output_path=output_dir / "ranker.lgbm")
        click.echo(f"  {summary['total_candidates']} candidates, "
                   f"{summary['total_groups']} groups")
    elif model == "cutoff":
        from cpl_lab.train_cutoff import train_cutoff

        click.echo("=== Training Cutoff ===")
        summary = train_cutoff(merged_dir=merged_dir, output_path=output_dir / "cutoff.lgbm")
        click.echo(f"  {summary['cutoff_rows']} rows")
