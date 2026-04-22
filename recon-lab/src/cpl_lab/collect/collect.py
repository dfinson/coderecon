"""Collect retrieval signals — Rich UI with per-worker progress."""

from __future__ import annotations

import os
import time
from pathlib import Path

import click
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text


console = Console()


def _iter_repos(data_dir: Path, repo_set: str) -> list[str]:
    """List repo-instance IDs for signal collection."""
    from cpl_lab.data_manifest import iter_repo_data_dirs, iter_task_json_files, repo_set_for_dir

    train_sets = {"ranker-gate", "cutoff"}
    allowed = {repo_set} if repo_set != "all" else train_sets
    return sorted(
        repo_dir.name for repo_dir in iter_repo_data_dirs(data_dir)
        if repo_set_for_dir(repo_dir) in allowed
        and (
            (repo_dir / "ground_truth" / "queries.jsonl").exists()
            or iter_task_json_files(repo_dir / "ground_truth")
            or (repo_dir / "ground_truth.jsonl").exists()
        )
    )


def _find_clone_dir(clones_dir: Path, repo_dir: Path) -> Path | None:
    """Resolve the instance worktree clone directory."""
    from cpl_lab.data_manifest import clone_dir_for_dir

    return clone_dir_for_dir(repo_dir, clones_dir)


def _find_main_clone_dir(clones_dir: Path, repo_dir: Path) -> Path | None:
    """Resolve the main repo clone directory (owns .recon/index.db)."""
    from cpl_lab.data_manifest import main_clone_dir_for_dir

    return main_clone_dir_for_dir(repo_dir, clones_dir)


def _ensure_ground_truth_tables(repo_id: str, repo_dir: Path, clone_dir: Path) -> None:
    """Post-process raw task JSONs into JSONL tables when needed."""
    from cpl_lab.data_manifest import iter_task_json_files
    from cpl_lab.collect.merge_ground_truth import collect_ground_truth

    gt_dir = repo_dir / "ground_truth"
    if (gt_dir / "queries.jsonl").exists() and (gt_dir / "touched_objects.jsonl").exists():
        return
    if not iter_task_json_files(gt_dir):
        return

    index_db = clone_dir / ".recon" / "index.db"
    if not index_db.exists():
        raise FileNotFoundError(f"index.db not found: {index_db}")

    collect_ground_truth(repo_id, repo_dir, index_db)


def run_collect(
    data_dir: Path,
    clones_dir: Path,
    repo_set: str = "all",
    repo: str | None = None,
    workers: int = 0,
    verbose: bool = False,
) -> None:
    """Collect retrieval signals — direct in-process, multi-worker."""
    from cpl_lab.collect.collect_signals import collect_all

    repo_ids = [repo] if repo else _iter_repos(data_dir, repo_set)
    if not repo_ids:
        console.print("[yellow]No repos with ground truth found.[/yellow]")
        return

    jobs: list[tuple[str, Path, Path, Path]] = []
    skipped = 0
    for rid in repo_ids:
        # Skip repos that already have completed signals
        sig_dir = data_dir / rid / "signals"
        if (sig_dir / "summary.json").exists() and (sig_dir / "candidates_rank.parquet").exists():
            skipped += 1
            continue
        repo_dir = data_dir / rid
        instance_dir = _find_clone_dir(clones_dir, repo_dir)
        main_dir = _find_main_clone_dir(clones_dir, repo_dir)
        if main_dir and (main_dir / ".recon" / "index.db").exists():
            try:
                _ensure_ground_truth_tables(rid, repo_dir, main_dir)
            except Exception as exc:
                if verbose:
                    console.print(f"[red]Skipping {rid}: GT postprocess failed: {exc}[/red]")
                continue
            # instance_dir may be the same as main_dir for non-worktree repos
            jobs.append((rid, repo_dir, main_dir, instance_dir or main_dir))

    if skipped:
        console.print(f"[dim]Skipping {skipped} repos with completed signals.[/dim]")

    if not jobs:
        console.print("[yellow]No indexed repos ready for collection.[/yellow]")
        return

    if workers <= 0:
        workers = min(os.cpu_count() or 6, 6)

    total = len(jobs)

    # ── Rich progress ────────────────────────────────────────────
    # Overall bar
    overall = Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=30, complete_style="green"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console, expand=False,
    )
    overall_bar = overall.add_task("Overall", total=total)

    # Per-worker bars (shows active repos)
    worker_progress = Progress(
        TextColumn("  {task.description}", style="dim"),
        BarColumn(bar_width=25, style="cyan", complete_style="green"),
        TaskProgressColumn(),
        console=console, expand=False,
    )
    # Track active worker tasks: repo_id -> task_id
    worker_tasks: dict[str, object] = {}

    # Completed table
    tbl = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    tbl.add_column("Repo", style="cyan", min_width=28)
    tbl.add_column("Queries", justify="right", min_width=7)
    tbl.add_column("Candidates", justify="right", min_width=10)
    tbl.add_column("Time", justify="right", min_width=7)
    tbl.add_column("", width=4)

    ok = failed = tot_q = tot_c = 0
    t_start = time.monotonic()

    def _render() -> Panel:
        header = Text.from_markup(
            f"  [bold]{total}[/bold] repos  ·  [bold]{workers}[/bold] workers"
            f"  ·  {ok}[green] ok[/green]  {failed}[red] fail[/red]"
            f"  ·  {tot_q:,} queries → {tot_c:,} candidates"
        )
        parts = [header, Text(""), overall]
        # Only show worker bars if there are active tasks
        if worker_progress.tasks:
            parts.append(worker_progress)
        if tbl.rows:
            parts.extend([Text(""), tbl])
        return Panel(Group(*parts),
                     title="Signal Collection", title_align="left",
                     border_style="dim", width=72, padding=(0, 1))

    _last_refresh = 0.0

    def on_progress(repo_id: str, done: int, total_q: int) -> None:
        nonlocal _last_refresh
        if repo_id not in worker_tasks:
            tid = worker_progress.add_task(repo_id, total=total_q)
            worker_tasks[repo_id] = tid
        worker_progress.update(worker_tasks[repo_id], completed=done, total=total_q)
        # Throttle UI refreshes to avoid flicker
        now = time.monotonic()
        if now - _last_refresh > 0.5:
            _last_refresh = now
            live.update(_render())

    def on_done(s: dict) -> None:
        nonlocal ok, failed, tot_q, tot_c
        rid = s["repo_id"]
        q, c = s["queries_processed"], s["total_candidates"]
        sec = s.get("elapsed_sec", 0)

        # Remove worker bar
        if rid in worker_tasks:
            worker_progress.remove_task(worker_tasks[rid])
            del worker_tasks[rid]

        if s.get("status") == "ok":
            ok += 1; tot_q += q; tot_c += c
            mark = "[green]✓[/green]"
        else:
            failed += 1
            mark = "[red]✗[/red]"
            err = s.get("error", "")
            if err:
                rid = f"{rid} [dim red]{err[:50]}[/dim red]"

        tbl.add_row(rid, f"{q}", f"{c:,}", f"{sec}s", mark)
        overall.update(overall_bar, advance=1)
        live.update(_render())

    with Live(_render(), console=console, refresh_per_second=4) as live:
        collect_all(repo_jobs=jobs, workers=workers,
                    on_progress=on_progress, on_complete=on_done)

    # Final summary
    elapsed = round(time.monotonic() - t_start, 1)
    console.print()
    console.print(Panel(
        Text.from_markup(
            f"[green]✓[/green] {ok} collected  [red]{failed}[/red] failed"
            f"  ({elapsed}s)\n"
            f"  {tot_q:,} queries → {tot_c:,} candidates"
        ),
        title="Done", title_align="left",
        border_style="green" if failed == 0 else "yellow", width=60,
    ))
