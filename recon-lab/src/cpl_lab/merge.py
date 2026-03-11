"""Merge ground truth and signals — CLI adapter for existing merge modules."""

from __future__ import annotations

from pathlib import Path

import click


def run_merge(data_dir: Path, what: str = "all", verbose: bool = False) -> None:
    """Merge per-repo data into unified parquet tables."""
    if what in ("gt", "all"):
        click.echo("=== Merging Ground Truth ===")
        from cpl_lab.merge_ground_truth import merge_ground_truth

        summary = merge_ground_truth(data_dir)
        for table, count in summary["counts"].items():
            click.echo(f"  {table}: {count} rows")

    if what in ("signals", "all"):
        click.echo("\n=== Merging Signals ===")
        from cpl_lab.merge_signals import merge_signals

        summary = merge_signals(data_dir)
        click.echo(f"  {summary['total_candidates']} candidates, "
                   f"positive rate: {summary['positive_rate']:.3f}")

    click.echo("\nMerge complete.")
