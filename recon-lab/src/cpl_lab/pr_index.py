"""PR index — register main repos with daemon, index worktrees.

Pass 2: Start daemon, register + index all main repos.
Pass 3: For each PR worktree, run ``recon init`` to build a per-worktree
        index.db, then register the worktree with the daemon.

The daemon owns the main repo lifecycle (catalog, file watcher, incremental
reindex).  Each PR worktree gets its own ``.recon/index.db`` via ``recon init``
so that ``collect_signals`` can create a standalone ``AppContext`` per worktree
— the same pattern as the SWE-bench pipeline.  The daemon is notified of
worktrees so they appear in catalog queries and get file-watching.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import click

from cpl_lab.clone import REPO_MANIFEST, REPO_SETS, clone_dir_for


def _recon_python() -> str:
    """Find the Python interpreter that can run the coderecon CLI."""
    coderecon_root = Path(__file__).resolve().parents[3]
    venv = coderecon_root / ".venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    return sys.executable


def _recon_cmd(*args: str) -> list[str]:
    """Build a coderecon CLI command."""
    return [_recon_python(), "-m", "coderecon.cli.main", *args]


def _run(cmd: list[str], *, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)


def _daemon_running() -> tuple[bool, int | None]:
    """Check if the global daemon is running.  Returns (running, port)."""
    pid_file = Path.home() / ".coderecon" / "daemon.pid"
    port_file = Path.home() / ".coderecon" / "daemon.port"
    if not pid_file.exists() or not port_file.exists():
        return False, None
    try:
        pid = int(pid_file.read_text().strip())
        port = int(port_file.read_text().strip())
        # Check if process is alive
        import os
        os.kill(pid, 0)
        return True, port
    except (ValueError, OSError):
        return False, None


def _wait_for_daemon(timeout: int = 30) -> int:
    """Wait for daemon to be reachable.  Returns port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        running, port = _daemon_running()
        if running and port:
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/health",
                    headers={"Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        return port
            except Exception:
                pass
        time.sleep(1)
    raise click.ClickException("Daemon did not start within timeout")


def _start_daemon() -> int:
    """Ensure the daemon is running.  Returns port."""
    running, port = _daemon_running()
    if running and port:
        click.echo(f"Daemon already running on port {port}")
        return port

    click.echo("Starting daemon...")
    # Start in background
    subprocess.Popen(
        _recon_cmd("up"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    port = _wait_for_daemon()
    click.echo(f"Daemon running on port {port}")
    return port


def _register_repo(repo_path: Path) -> None:
    """Register a main repo via CLI."""
    result = _run(_recon_cmd("register", str(repo_path)), check=False, timeout=1800)
    if result.returncode != 0:
        click.echo(f"    Register failed: {result.stderr[:200]}")


def _register_worktree(wt_path: Path) -> None:
    """Register a worktree via CLI."""
    result = _run(_recon_cmd("register-worktree", str(wt_path)), check=False, timeout=60)
    if result.returncode != 0:
        click.echo(f"    Worktree register failed: {result.stderr[:200]}")


def _wait_repo_idle(port: int, repo_name: str, timeout: int = 600) -> bool:
    """Poll daemon until a repo's indexer is idle."""
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/repos/{repo_name}/status",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                indexer = data.get("indexer", {})
                state = indexer.get("state", "unknown")
                queue = indexer.get("queue_size", 0)
                if state == "idle" and queue == 0:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def run_index_main(
    clones_dir: Path,
    repo_set: str = "all",
    repo: str | None = None,
    verbose: bool = False,
) -> None:
    """Pass 2: Start daemon and index all main repos."""
    port = _start_daemon()

    if repo:
        repo_ids = [repo]
    else:
        allowed = set(REPO_SETS.keys()) if repo_set == "all" else {repo_set}
        repo_ids = [
            rid for rid, info in REPO_MANIFEST.items()
            if info["set"] in allowed
        ]

    click.echo(f"\nRegistering {len(repo_ids)} main repos...")

    for i, rid in enumerate(sorted(repo_ids), 1):
        cd = clone_dir_for(rid, clones_dir)
        if cd is None or not cd.is_dir():
            click.echo(f"  [{i}/{len(repo_ids)}] {rid}: SKIP (no clone)")
            continue

        click.echo(f"  [{i}/{len(repo_ids)}] {rid}: registering...")
        _register_repo(cd)

    # Wait for all repos to finish indexing
    click.echo("\nWaiting for indexing to complete...")
    for i, rid in enumerate(sorted(repo_ids), 1):
        cd = clone_dir_for(rid, clones_dir)
        if cd is None or not cd.is_dir():
            continue
        # The repo name in the daemon is derived from the directory name
        repo_name = cd.name
        if verbose:
            click.echo(f"  Waiting for {repo_name}...")
        if not _wait_repo_idle(port, repo_name, timeout=1800):
            click.echo(f"  WARNING: {repo_name} did not finish indexing in 30min")

    click.echo("All main repos indexed.")


def _init_worktree(wt_path: Path) -> bool:
    """Run ``recon init`` on a worktree to build its own index.db."""
    result = _run(_recon_cmd("init", str(wt_path)), check=False, timeout=600)
    if result.returncode != 0:
        click.echo(f"    Init failed: {result.stderr[:200]}")
        return False
    return True


def run_index_worktrees(
    clones_dir: Path,
    data_dir: Path,
    instances_dir: Path | None = None,
    repo_set: str = "all",
    repo: str | None = None,
    verbose: bool = False,
) -> None:
    """Pass 3: Run ``recon init`` on each PR worktree, then register with daemon.

    Each worktree gets its own ``.recon/index.db`` so collect_signals can
    create standalone AppContext instances.  After init, the worktree is
    registered with the running daemon for catalog visibility.
    """
    running, port = _daemon_running()
    if not running or not port:
        raise click.ClickException("Daemon not running. Run index-main first.")

    if instances_dir is None:
        instances_dir = clones_dir / "instances"

    # Load PR instances
    pr_file = data_dir / "pr_instances.jsonl"
    if not pr_file.exists():
        raise click.ClickException(f"PR instances file not found: {pr_file}")

    # Group instances by repo
    by_repo: dict[str, list[dict]] = {}
    for line in pr_file.read_text().splitlines():
        if not line.strip():
            continue
        inst = json.loads(line)
        if repo and inst["repo_id"] != repo:
            continue
        if repo_set != "all" and inst.get("repo_set") != repo_set:
            continue
        by_repo.setdefault(inst["repo_id"], []).append(inst)

    total_wt = sum(len(insts) for insts in by_repo.values())
    click.echo(f"Indexing {total_wt} worktrees across {len(by_repo)} repos...")

    done = ok = skip = fail = 0
    for rid in sorted(by_repo):
        insts = by_repo[rid]
        click.echo(f"\n  {rid}: {len(insts)} worktrees")

        for inst in insts:
            iid = inst["instance_id"]
            wt_dir = instances_dir / iid
            done += 1

            if not (wt_dir / ".git").exists():
                click.echo(f"    {iid}: SKIP (worktree not created)")
                fail += 1
                continue

            # Skip if already indexed
            if (wt_dir / ".recon" / "index.db").is_file():
                skip += 1
                if verbose:
                    click.echo(f"    {iid}: already indexed")
                continue

            if verbose or done % 10 == 0:
                click.echo(f"    [{done}/{total_wt}] indexing {iid}...")

            if _init_worktree(wt_dir):
                ok += 1
                # Register with daemon for catalog visibility
                _register_worktree(wt_dir)
            else:
                fail += 1

    click.echo(f"\nDone: {ok} indexed, {skip} existed, {fail} failed")
