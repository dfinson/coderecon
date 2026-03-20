"""PR-mining ground truth pipeline — replaces the AI orchestrator.

Fetches merged PRs with linked issues from GitHub, extracts ground truth
from diffs, maps to definitions in the coderecon index, and outputs
task JSON files in the same schema as the old ``generate`` pipeline.

Usage:
    cpl-lab mine                         # all repos, all sets
    cpl-lab mine --repo python-flask     # single repo
    cpl-lab mine --set ranker-gate       # one set
    cpl-lab mine --max-prs 50            # limit PRs per repo
    cpl-lab mine --no-filter             # skip LLM candidate filtering
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from cpl_lab.clone import REPO_MANIFEST, clone_dir_for
from cpl_lab.pr_to_ground_truth import (
    DefEntry,
    assemble_task_json,
    generate_queries,
    map_hunks_to_defs,
    parse_unified_diff,
)

logger = logging.getLogger(__name__)
console = Console()

# ── GitHub API via gh CLI ────────────────────────────────────────

# Fields requested from `gh pr list`
_PR_FIELDS = (
    "number,title,body,state,mergedAt,baseRefName,"
    "additions,deletions,changedFiles,"
    "closingIssuesReferences"
)


def _gh_json(args: list[str], timeout: int = 60) -> Any:
    """Run a `gh` CLI command and parse JSON output."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _owner_repo(repo_id: str) -> tuple[str, str] | None:
    """Extract (owner, repo_name) from the REPO_MANIFEST GitHub URLs."""
    # Walk the repo lists to find the URL for this repo_id
    from cpl_lab.clone import RANKER_GATE, CUTOFF, EVAL

    entry = REPO_MANIFEST.get(repo_id)
    if entry is None:
        return None

    clone_name = entry["clone_name"]
    for repo_list in (RANKER_GATE, CUTOFF, EVAL):
        for url, _ in repo_list:
            if url.rstrip("/").endswith(f"/{clone_name}"):
                parts = url.rstrip("/").split("/")
                return parts[-2], parts[-1]
    return None


def fetch_prs(
    owner: str,
    repo_name: str,
    max_prs: int = 100,
) -> list[dict[str, Any]]:
    """Fetch merged PRs with linked issues from GitHub."""
    try:
        prs = _gh_json([
            "pr", "list",
            "--repo", f"{owner}/{repo_name}",
            "--state", "merged",
            "--limit", str(max_prs),
            "--json", _PR_FIELDS,
        ], timeout=120)
    except Exception as e:
        logger.warning("Failed to fetch PRs for %s/%s: %s", owner, repo_name, e)
        return []

    return prs


def fetch_issue_body(owner: str, repo_name: str, issue_number: int) -> str:
    """Fetch the body for a linked issue referenced by a PR."""
    try:
        issue = _gh_json([
            "issue", "view", str(issue_number),
            "--repo", f"{owner}/{repo_name}",
            "--json", "body",
        ], timeout=60)
    except Exception:
        return ""
    return issue.get("body", "") or ""


def fetch_pr_diff(owner: str, repo_name: str, pr_number: int) -> str:
    """Fetch the unified diff for a specific PR."""
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr_number),
         "--repo", f"{owner}/{repo_name}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch diff for PR #{pr_number}")
    return result.stdout


# ── PR filtering ─────────────────────────────────────────────────


def filter_prs(prs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter PRs to those suitable for GT extraction.

    Criteria:
    - Has at least one closing issue reference
    - Changed ≤ 20 files (not a bulk refactor)
    - At least one linked issue body, fetched issue body, or PR body has ≥ 50
      characters (prevents title-only tasks that contaminate GT quality)
    """
    good: list[dict[str, Any]] = []

    for pr in prs:
        issues = pr.get("closingIssuesReferences", [])
        if not issues:
            continue

        changed_files = pr.get("changedFiles", 0)
        if changed_files > 20 or changed_files == 0:
            continue

        # Require a substantive task description (≥ 50 chars) from any source.
        # resolve_issue_text() falls back to the PR title when nothing else
        # reaches this threshold, so check the sources directly here.
        has_body = any(len((i.get("body", "") or "")) >= 50 for i in issues)
        if not has_body:
            pr_body = pr.get("body", "") or ""
            has_body = len(pr_body) >= 50
        if not has_body:
            continue

        good.append(pr)

    return good


def resolve_issue_text(owner: str, repo_name: str, pr: dict[str, Any]) -> str:
    """Resolve the best available task text from linked issues or PR body."""
    for issue in pr.get("closingIssuesReferences", []):
        body = issue.get("body", "") or ""
        if len(body) >= 50:
            return body

        number = issue.get("number")
        if isinstance(number, int):
            fetched_body = fetch_issue_body(owner, repo_name, number)
            if len(fetched_body) >= 50:
                return fetched_body

    pr_body = pr.get("body", "") or ""
    if len(pr_body) >= 50:
        return pr_body
    return pr["title"]


# ── Single-repo mining ───────────────────────────────────────────


def mine_repo(
    repo_id: str,
    data_dir: Path,
    clones_dir: Path,
    max_prs: int = 100,
    no_filter: bool = False,
    llm_model: str = "claude-haiku-4.5",
) -> dict[str, Any]:
    """Mine PRs for a single repo and write GT JSON files.

    Returns summary dict with counts.
    """
    t0 = time.monotonic()

    # Resolve GitHub owner/repo
    ident = _owner_repo(repo_id)
    if ident is None:
        return {"repo_id": repo_id, "status": "error", "error": "not in manifest",
                "tasks": 0, "elapsed_sec": 0}
    owner, repo_name = ident

    # Resolve clone directory and index
    clone = clone_dir_for(repo_id, clones_dir)
    if clone is None or not clone.is_dir():
        return {"repo_id": repo_id, "status": "error", "error": "clone not found",
                "tasks": 0, "elapsed_sec": 0}

    index_db = clone / ".recon" / "index.db"
    if not index_db.exists():
        return {"repo_id": repo_id, "status": "error", "error": "index.db not found",
                "tasks": 0, "elapsed_sec": 0}

    # Fetch and filter PRs
    prs = fetch_prs(owner, repo_name, max_prs=max_prs)
    good_prs = filter_prs(prs)

    if not good_prs:
        return {"repo_id": repo_id, "status": "skip", "error": "no qualifying PRs",
                "tasks": 0, "prs_fetched": len(prs), "elapsed_sec": 0}

    # Setup output directory
    gt_dir = data_dir / repo_id / "ground_truth"
    gt_dir.mkdir(parents=True, exist_ok=True)

    tasks_written = 0
    errors = 0
    total_min_suff = 0
    total_thrash_prev = 0
    total_llm_calls = 0

    for pr in good_prs:
        pr_number = pr["number"]
        task_id = f"PR-{pr_number}"

        # Skip if already generated
        out_path = gt_dir / f"{task_id}.json"
        if out_path.exists():
            tasks_written += 1
            continue

        try:
            # Fetch full diff
            diff_text = fetch_pr_diff(owner, repo_name, pr_number)

            # Parse diff
            file_diffs = parse_unified_diff(diff_text)
            if not file_diffs:
                continue

            # Map hunks to definitions
            min_suff, thrash_prev, excluded = map_hunks_to_defs(file_diffs, index_db)

            if not min_suff:
                # PR changes lines that don't overlap any indexed defs
                # (e.g., whitespace-only, config files, docs)
                continue

            issue_body = resolve_issue_text(owner, repo_name, pr)

            # ── LLM / heuristic filtering of thrash_preventing candidates ──
            min_suff_dicts = [
                {"path": d.path, "name": d.name, "kind": d.kind,
                 "start_line": d.start_line, "end_line": d.end_line,
                 "reason": d.reason}
                for d in min_suff
            ]

            if no_filter:
                from cpl_lab.llm_filter import heuristic_filter_only
                filt = heuristic_filter_only(min_suff_dicts, thrash_prev)
            else:
                from cpl_lab.llm_filter import filter_candidates
                filt = filter_candidates(
                    issue_title=pr["title"],
                    issue_body=issue_body,
                    min_suff_defs=min_suff_dicts,
                    thrash_prev_defs=thrash_prev,
                    model=llm_model,
                )
                total_llm_calls += filt.llm_calls

            # Rebuild thrash_prev from filtered results
            thrash_prev = [
                DefEntry(
                    path=d["path"], name=d["name"], kind=d["kind"],
                    start_line=d["start_line"], end_line=d["end_line"],
                    reason=d.get("reason", ""),
                ) if isinstance(d, dict) else d
                for d in filt.kept
            ]

            # Generate queries
            changed_paths = [fd.path for fd in file_diffs if not fd.is_deleted]
            queries = generate_queries(
                issue_title=pr["title"],
                issue_body=issue_body,
                min_suff_defs=min_suff,
                changed_paths=changed_paths,
                diff_text=diff_text,
            )

            # Assemble and write task JSON
            task_json = assemble_task_json(
                task_id=task_id,
                pr_number=pr_number,
                pr_title=pr["title"],
                issue_body=issue_body,
                diff_text=diff_text,
                file_diffs=file_diffs,
                min_suff=min_suff,
                thrash_prev=thrash_prev,
                excluded=excluded,
                queries=queries,
            )

            out_path.write_text(json.dumps(task_json, indent=2))
            tasks_written += 1
            total_min_suff += len(min_suff)
            total_thrash_prev += len(thrash_prev)

        except Exception as e:
            logger.warning("Error processing PR #%d for %s: %s", pr_number, repo_id, e)
            errors += 1
            continue

    elapsed = round(time.monotonic() - t0, 1)
    summary = {
        "repo_id": repo_id,
        "status": "ok",
        "prs_fetched": len(prs),
        "prs_qualifying": len(good_prs),
        "tasks": tasks_written,
        "min_suff_defs": total_min_suff,
        "thrash_prev_defs": total_thrash_prev,
        "llm_calls": total_llm_calls,
        "errors": errors,
        "elapsed_sec": elapsed,
    }

    # Write summary
    (data_dir / repo_id / "mine_summary.json").write_text(
        json.dumps(summary, indent=2)
    )

    return summary


# ── Multi-repo orchestrator with Rich UI ─────────────────────────


def _iter_repos(repo_set: str) -> list[str]:
    """List repo IDs for mining."""
    if repo_set == "all":
        return sorted(REPO_MANIFEST.keys())
    return sorted(
        rid for rid, info in REPO_MANIFEST.items()
        if info.get("set") == repo_set
    )


def run_mine(
    data_dir: Path,
    clones_dir: Path,
    repo_set: str = "all",
    repo: str | None = None,
    max_prs: int = 100,
    no_filter: bool = False,
    llm_model: str = "claude-haiku-4.5",
    verbose: bool = False,
) -> None:
    """Run PR mining across repos with Rich progress display."""
    repo_ids = [repo] if repo else _iter_repos(repo_set)
    if not repo_ids:
        console.print("[yellow]No repos found for mining.[/yellow]")
        return

    # Filter to repos with clones and indexes
    jobs: list[str] = []
    for rid in repo_ids:
        clone = clone_dir_for(rid, clones_dir)
        if clone and (clone / ".recon" / "index.db").exists():
            jobs.append(rid)
        elif verbose:
            console.print(f"  [dim]Skipping {rid}: no index[/dim]")

    if not jobs:
        console.print("[yellow]No indexed repos available for mining.[/yellow]")
        return

    # Progress display
    progress = Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=30, complete_style="green"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    )
    overall = progress.add_task("Mining PRs", total=len(jobs))

    tbl = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    tbl.add_column("Repo", style="cyan", min_width=28)
    tbl.add_column("PRs", justify="right", min_width=6)
    tbl.add_column("Tasks", justify="right", min_width=6)
    tbl.add_column("Defs", justify="right", min_width=6)
    tbl.add_column("Time", justify="right", min_width=7)
    tbl.add_column("", width=4)

    ok = failed = tot_tasks = 0
    t_start = time.monotonic()

    with progress:
        for rid in jobs:
            summary = mine_repo(
                repo_id=rid,
                data_dir=data_dir,
                clones_dir=clones_dir,
                max_prs=max_prs,
                no_filter=no_filter,
                llm_model=llm_model,
            )

            status = summary.get("status", "error")
            tasks = summary.get("tasks", 0)
            defs = summary.get("min_suff_defs", 0) + summary.get("thrash_prev_defs", 0)
            elapsed = summary.get("elapsed_sec", 0)
            prs = summary.get("prs_qualifying", 0)

            if status == "ok" and tasks > 0:
                ok += 1
                tot_tasks += tasks
                mark = "[green]✓[/green]"
            elif status == "skip":
                mark = "[yellow]–[/yellow]"
            else:
                failed += 1
                mark = "[red]✗[/red]"

            tbl.add_row(rid, f"{prs}", f"{tasks}", f"{defs}", f"{elapsed}s", mark)
            progress.update(overall, advance=1)

    # Print results table
    console.print()
    console.print(tbl)

    elapsed_total = round(time.monotonic() - t_start, 1)
    console.print()
    console.print(Panel(
        Text.from_markup(
            f"[green]✓[/green] {ok} repos mined  "
            f"[red]{failed}[/red] failed  "
            f"({elapsed_total}s)\n"
            f"  {tot_tasks} total tasks generated"
        ),
        title="Mining Complete",
        title_align="left",
        border_style="green" if failed == 0 else "yellow",
        width=60,
    ))
