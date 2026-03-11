"""Collect retrieval signals — CLI adapter for collect_signals.py."""

from __future__ import annotations

from pathlib import Path

import click


def _iter_repos(data_dir: Path, clones_dir: Path, repo_set: str, repo: str | None) -> list[str]:
    """List repo IDs that have ground truth ready for signal collection."""
    if repo:
        return [repo]

    repo_ids = []
    if repo_set == "all":
        set_dirs = sorted(d for d in clones_dir.iterdir() if d.is_dir())
    else:
        set_dirs = [clones_dir / repo_set]

    for set_dir in set_dirs:
        if not set_dir.is_dir():
            continue
        for repo_dir in sorted(set_dir.iterdir()):
            if not repo_dir.is_dir():
                continue
            rid = repo_dir.name
            # Only collect if ground truth queries exist
            queries = data_dir / rid / "ground_truth" / "queries.jsonl"
            if queries.exists():
                repo_ids.append(rid)
    return repo_ids


def run_collect(
    data_dir: Path,
    clones_dir: Path,
    repo_set: str = "all",
    repo: str | None = None,
    mcp_url: str = "http://127.0.0.1:7654/mcp",
    verbose: bool = False,
) -> None:
    """Collect retrieval signals for repos with ground truth."""
    from cpl_lab.collect_signals import collect_signals

    repo_ids = _iter_repos(data_dir, clones_dir, repo_set, repo)
    if not repo_ids:
        click.echo("No repos with ground truth found for signal collection.")
        return

    click.echo(f"Collecting signals for {len(repo_ids)} repos...")
    ok = failed = 0

    for rid in repo_ids:
        click.echo(f"\n=== {rid} ===")
        try:
            summary = collect_signals(
                repo_id=rid,
                data_dir=data_dir / rid,
                mcp_url=mcp_url,
            )
            click.echo(f"  {summary['queries_processed']} queries, "
                       f"{summary['total_candidates']} candidates")
            ok += 1
        except Exception as e:
            click.echo(f"  ERROR: {e}", err=True)
            failed += 1

    click.echo(f"\nDone: {ok} collected, {failed} failed")
