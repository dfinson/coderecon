"""PR checkout — create git worktrees for selected PR instances.

Reads ``pr_instances.jsonl`` and creates a detached worktree per PR at
the base commit.  Pure git operations — no daemon, no indexing.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import click

from cpl_lab.pipeline.clone import clone_dir_for


def _git(
    args: list[str], cwd: Path | None = None, check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check,
        capture_output=True, text=True,
    )


def _create_worktree(clone_dir: Path, worktree_dir: Path, commit: str) -> bool:
    """Create a detached worktree at *commit*.  Returns True on success."""
    if worktree_dir.is_dir():
        # Validate existing worktree: .git must exist and git rev-parse must succeed
        git_file = worktree_dir / ".git"
        if git_file.exists():
            probe = _git(["rev-parse", "HEAD"], cwd=worktree_dir, check=False)
            if probe.returncode == 0:
                return True
            # Corrupt worktree — remove and recreate
            click.echo(f"    Removing corrupt worktree: {worktree_dir.name}")
        # Directory exists but isn't a valid worktree — prune and remove
        _git(["worktree", "prune"], cwd=clone_dir, check=False)
        subprocess.run(["rm", "-rf", str(worktree_dir)], check=False)

    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    result = _git(
        ["worktree", "add", "--detach", str(worktree_dir), commit],
        cwd=clone_dir,
        check=False,
    )
    if result.returncode != 0:
        click.echo(f"    FAILED: {result.stderr.strip()[:200]}")
        return False
    return True


def run_pr_checkout(
    clones_dir: Path,
    data_dir: Path,
    instances_dir: Path | None = None,
    repo_set: str = "all",
    repo: str | None = None,
    verbose: bool = False,
) -> None:
    """Create worktrees for all PR instances in pr_instances.jsonl."""
    pr_file = data_dir / "pr_instances.jsonl"
    if not pr_file.exists():
        raise click.ClickException(f"PR instances file not found: {pr_file}")

    if instances_dir is None:
        instances_dir = clones_dir / "instances"

    # Load instances
    instances: list[dict] = []
    for line in pr_file.read_text().splitlines():
        if not line.strip():
            continue
        inst = json.loads(line)
        # Filter by set/repo
        if repo and inst["repo_id"] != repo:
            continue
        if repo_set != "all" and inst.get("repo_set") != repo_set:
            continue
        instances.append(inst)

    if not instances:
        click.echo("No instances to checkout.")
        return

    click.echo(f"Creating {len(instances)} worktrees...")
    ok = skip = fail = 0

    for i, inst in enumerate(instances, 1):
        iid = inst["instance_id"]
        rid = inst["repo_id"]
        base = inst["base_commit"]
        wt_dir = instances_dir / iid

        if verbose or i % 50 == 0 or i == len(instances):
            click.echo(f"  [{i}/{len(instances)}] {iid}")

        cd = clone_dir_for(rid, clones_dir)
        if cd is None or not cd.is_dir():
            if verbose:
                click.echo(f"    Skipping — clone not found for {rid}")
            fail += 1
            continue

        # Check if worktree already exists
        if (wt_dir / ".git").exists():
            skip += 1
            continue

        if _create_worktree(cd, wt_dir, base):
            ok += 1
        else:
            fail += 1

    click.echo(f"\nDone: {ok} created, {skip} existed, {fail} failed")
