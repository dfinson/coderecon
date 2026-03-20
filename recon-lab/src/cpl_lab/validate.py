"""Validate ground truth — CLI adapter for validate_ground_truth.py."""

from __future__ import annotations

from pathlib import Path

import click

from cpl_lab.data_manifest import iter_repo_data_dirs, repo_set_for_dir


def run_validate(
    data_dir: Path,
    repo: str | None = None,
    repo_set: str = "all",
    verbose: bool = False,
) -> None:
    """Validate ground truth JSON files for repos."""
    from cpl_lab.validate_ground_truth import validate_repo

    # Find repo directories to validate
    repo_dirs: list[Path] = []
    if repo:
        rd = data_dir / repo
        if not rd.is_dir():
            raise click.ClickException(f"Repo data not found: {rd}")
        repo_dirs = [rd]
    else:
        for repo_dir in iter_repo_data_dirs(data_dir):
            gt = repo_dir / "ground_truth"
            if gt.is_dir() and any(gt.glob("*.json")):
                if repo_set == "all" or repo_set_for_dir(repo_dir) == repo_set:
                    repo_dirs.append(repo_dir)

    if not repo_dirs:
        click.echo("No repos with ground truth found to validate.")
        return

    ok = failed = 0
    for rd in repo_dirs:
        errors = validate_repo(rd)
        if errors:
            click.echo(f"FAIL {rd.name} — {len(errors)} error(s)")
            if verbose:
                for e in errors[:10]:
                    click.echo(f"  {e}")
            failed += 1
        else:
            click.echo(f"  OK {rd.name}")
            ok += 1

    click.echo(f"\nValidated: {ok} passed, {failed} failed")
    if failed:
        raise SystemExit(1)
