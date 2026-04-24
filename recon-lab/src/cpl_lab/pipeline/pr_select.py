"""PR selection — fetch merged PRs from GitHub and filter for GT suitability.

For each repo in REPO_MANIFEST:
  1. Fetch merged PR metadata via GitHub API.
  2. Compute diffs locally via ``git diff`` (full clones have history).
  3. Filter: non-trivial title/body, parseable diff, ≥1 def touched.
  4. Write ``pr_instances.jsonl`` with one row per selected PR.

Requires ``gh`` CLI authentication (``gh auth login``).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import click

from cpl_lab.pipeline.clone import (
    REPO_MANIFEST,
    REPO_SETS,
    clone_dir_for,
    github_slug_for,
    repo_set_for,
)

logger = logging.getLogger(__name__)

_API = "https://api.github.com"
_PER_PAGE = 100  # max allowed by GitHub


def _gh_headers() -> dict[str, str]:
    """Build GitHub API headers using ``gh auth token`` (gh CLI)."""
    import subprocess

    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, check=True,
        )
        token = result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise click.ClickException(
            "GitHub auth failed. Run 'gh auth login' first."
        )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_get(url: str, headers: dict[str, str], *, retries: int = 3) -> list[dict[str, Any]]:
    """GET a single page from the GitHub API, returning parsed JSON."""
    for attempt in range(retries):
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as exc:
            if exc.code == 403:
                # Rate limit — wait and retry
                reset = exc.headers.get("X-RateLimit-Reset")
                if reset:
                    wait = max(int(reset) - int(time.time()), 1)
                    click.echo(f"    Rate limited, waiting {wait}s...")
                    time.sleep(min(wait, 60))
                    continue
            if exc.code in (502, 503, 504) and attempt < retries - 1:
                click.echo(f"    HTTP {exc.code}, retrying in {2 ** attempt}s...")
                time.sleep(2 ** attempt)
                continue
            raise
        except (URLError, OSError) as exc:
            if attempt < retries - 1:
                click.echo(f"    Connection error ({exc}), retrying in {2 ** attempt}s...")
                time.sleep(2 ** attempt)
                continue
            raise
    return []  # unreachable, but keeps mypy happy


def _fetch_merged_prs(
    slug: str,
    headers: dict[str, str],
    max_prs: int = 200,
) -> list[dict[str, Any]]:
    """Fetch up to *max_prs* merged PRs for a repo, newest first."""
    prs: list[dict[str, Any]] = []
    page = 1
    while len(prs) < max_prs:
        url = (
            f"{_API}/repos/{slug}/pulls?"
            f"state=closed&sort=updated&direction=desc"
            f"&per_page={_PER_PAGE}&page={page}"
        )
        batch = _gh_get(url, headers)
        if not batch:
            break
        for pr in batch:
            if pr.get("merged_at"):
                prs.append(pr)
                if len(prs) >= max_prs:
                    break
        page += 1
        # Safety: stop pagination if page returned fewer than per_page
        if len(batch) < _PER_PAGE:
            break
    return prs


def _git_diff(clone_dir: Path, base: str, merge: str) -> str | None:
    """Compute diff between base and merge commit locally."""
    try:
        result = subprocess.run(
            ["git", "diff", f"{base}..{merge}"],
            cwd=clone_dir,
            capture_output=True,
            check=True,
            timeout=60,
        )
        return result.stdout.decode("utf-8", errors="replace")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def _git_merge_base(clone_dir: Path, base: str, merge: str) -> str | None:
    """Find actual merge-base between two commits."""
    try:
        result = subprocess.run(
            ["git", "merge-base", base, merge],
            cwd=clone_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def _git_has_commit(clone_dir: Path, sha: str) -> bool:
    """Check if a commit exists in the local repo."""
    result = subprocess.run(
        ["git", "cat-file", "-t", sha],
        cwd=clone_dir,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "commit"


def select_prs_for_repo(
    repo_id: str,
    clone_dir: Path,
    headers: dict[str, str],
    *,
    prs_per_repo: int = 30,
    min_files: int = 1,
    max_files: int = 50,
    min_body_chars: int = 20,
    max_fetch: int = 200,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Select PRs for one repo. Returns list of instance dicts."""
    from cpl_lab.pipeline.patch_ground_truth import map_hunks_to_defs, parse_unified_diff

    slug = github_slug_for(repo_id)
    if not slug:
        return []

    click.echo(f"  Fetching PRs for {slug}...")
    prs = _fetch_merged_prs(slug, headers, max_prs=max_fetch)
    click.echo(f"    {len(prs)} merged PRs fetched")

    selected: list[dict[str, Any]] = []

    for pr in prs:
        if len(selected) >= prs_per_repo:
            break

        number = pr["number"]
        title = (pr.get("title") or "").strip()
        body = (pr.get("body") or "").strip()
        base_sha = pr.get("base", {}).get("sha")
        merge_sha = pr.get("merge_commit_sha")

        # Filter: must have title, body, and shas
        if not title:
            continue
        if len(body) < min_body_chars:
            continue
        if not base_sha or not merge_sha:
            continue

        # Filter: both commits must exist in our full clone
        if not _git_has_commit(clone_dir, base_sha):
            continue
        if not _git_has_commit(clone_dir, merge_sha):
            continue

        # Compute actual merge-base (handles non-linear history)
        actual_base = _git_merge_base(clone_dir, base_sha, merge_sha)
        if not actual_base:
            continue

        # Get diff locally
        diff_text = _git_diff(clone_dir, actual_base, merge_sha)
        if not diff_text or len(diff_text) < 10:
            continue

        # Parse diff
        try:
            file_diffs = parse_unified_diff(diff_text)
        except Exception:
            continue
        if not file_diffs:
            continue

        # Filter: reasonable number of changed files (computed locally
        # since the list endpoint doesn't populate changed_files).
        changed = len(file_diffs)
        if changed < min_files or changed > max_files:
            continue

        # Check that diff touches at least 1 indexed def.
        # Uses the main repo's index.db (HEAD), not the worktree (which
        # doesn't exist yet). This is a pre-filter — pr_import re-maps
        # against the worktree index at the actual base commit.
        index_db = clone_dir / ".recon" / "index.db"
        if not index_db.exists():
            # Can't pre-filter without index, accept on diff alone
            gt_defs = file_diffs  # placeholder — will be validated later
        else:
            try:
                gt_defs = map_hunks_to_defs(file_diffs, index_db, "main")
            except Exception:
                gt_defs = []
            if not gt_defs:
                continue

        instance_id = f"{repo_id}_pr{number}"
        selected.append({
            "instance_id": instance_id,
            "repo_id": repo_id,
            "repo_set": repo_set_for(repo_id),
            "pr_number": number,
            "base_commit": actual_base,
            "merge_commit": merge_sha,
            "title": title,
            "body": body[:5000],
            "diff_text": diff_text,
            "files_changed": len(file_diffs),
            "gt_def_count": len(gt_defs) if isinstance(gt_defs, list) else 0,
        })

        if verbose:
            click.echo(f"    PR #{number}: {title[:60]}  ({changed} files)")

    click.echo(f"    Selected {len(selected)}/{len(prs)} PRs")
    return selected


def run_pr_select(
    clones_dir: Path,
    data_dir: Path,
    repo_set: str = "all",
    repo: str | None = None,
    prs_per_repo: int = 30,
    max_files: int = 50,
    verbose: bool = False,
) -> None:
    """Select PRs for all repos and write pr_instances.jsonl."""
    headers = _gh_headers()

    if repo:
        repo_ids = [repo]
    else:
        allowed = set(REPO_SETS.keys()) if repo_set == "all" else {repo_set}
        repo_ids = [
            rid for rid, info in REPO_MANIFEST.items()
            if info["set"] in allowed
        ]

    out_path = data_dir / "pr_instances.jsonl"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Load existing selections (tolerant of truncated trailing line)
    existing_rows: list[str] = []  # raw JSON lines for surviving rows
    existing: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # discard truncated line from prior crash
            existing.add(row["instance_id"])
            existing_rows.append(line)

    total_new = 0
    failed_repos: list[str] = []
    new_rows: list[str] = []

    for i, rid in enumerate(sorted(repo_ids), 1):
        click.echo(f"\n[{i}/{len(repo_ids)}] {rid}")
        cd = clone_dir_for(rid, clones_dir)
        if cd is None or not cd.is_dir():
            click.echo(f"  Skipping — clone not found")
            continue

        try:
            instances = select_prs_for_repo(
                rid, cd, headers,
                prs_per_repo=prs_per_repo,
                max_files=max_files,
                verbose=verbose,
            )
        except Exception as exc:
            click.echo(f"  ERROR: {exc}")
            failed_repos.append(rid)
            continue

        for inst in instances:
            if inst["instance_id"] in existing:
                continue
            row_line = json.dumps(inst, separators=(",", ":"))
            new_rows.append(row_line)
            existing.add(inst["instance_id"])
            total_new += 1

    # Atomic rewrite: merge existing + new, write to tmp, rename
    tmp_path = out_path.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        for line in existing_rows:
            f.write(line + "\n")
        for line in new_rows:
            f.write(line + "\n")
    os.replace(tmp_path, out_path)

    if failed_repos:
        click.echo(f"\n{len(failed_repos)} repos failed: {', '.join(failed_repos)}")
    click.echo(f"\nDone: {total_new} new PR instances written to {out_path}")
    click.echo(f"Total in file: {len(existing)}")
