"""Merge ground truth and signals — CLI adapter for existing merge modules."""

from __future__ import annotations

from pathlib import Path

import click


def run_merge(
    data_dir: Path,
    clones_dir: Path,
    what: str = "all",
    verbose: bool = False,
) -> None:
    """Merge per-repo data into unified parquet tables."""
    if what in ("gt", "all"):
        click.echo("=== Merging Ground Truth ===")
        from cpl_lab.merge_ground_truth import merge_ground_truth

        summary = merge_ground_truth(data_dir, clones_dir=clones_dir, verbose=verbose)
        for table, count in summary["counts"].items():
            click.echo(f"  {table}: {count} rows")

    if what in ("signals", "all"):
        from cpl_lab.merge_signals import merge_signals

        summary = merge_signals(data_dir)
        pos = summary['total_candidates'] * summary['positive_rate']
        click.echo(f"  {summary['total_candidates']:,} candidates merged, "
                   f"{int(pos):,} positive ({summary['positive_rate']*100:.4f}%)")

    click.echo("\nMerge complete.")
