"""recon status command - show daemon status."""

from __future__ import annotations

import json
from pathlib import Path

import click
import httpx
import structlog

from coderecon.cli.utils import find_repo_root
from coderecon.daemon.global_lifecycle import is_global_server_running, read_global_server_info

log = structlog.get_logger(__name__)

@click.command()
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def status_command(path: Path | None, as_json: bool) -> None:
    """Show CodeRecon daemon status.

    PATH is the repository root. If not specified, auto-detects by walking
    up from the current directory to find the git root.
    """
    repo_root = find_repo_root(path)

    coderecon_dir = repo_root / ".recon"
    if not coderecon_dir.exists():
        if as_json:
            click.echo(json.dumps({"initialized": False}))
        else:
            click.echo("Repository not initialized. Run 'recon init' first.")
        return

    if not is_global_server_running():
        if as_json:
            click.echo(json.dumps({"initialized": True, "running": False}))
        else:
            click.echo("Daemon: not running")
            click.echo(f"Repository: {repo_root}")
        return

    info = read_global_server_info()
    if info is None:
        if as_json:
            click.echo(json.dumps({"initialized": True, "running": False}))
        else:
            click.echo("Daemon: not running (stale PID file)")
        return

    pid, port = info

    # Query global daemon health to find this repo
    try:
        health_resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=5.0)
        health_data = health_resp.json()
    except (httpx.RequestError, json.JSONDecodeError) as e:
        if as_json:
            click.echo(json.dumps({
                "initialized": True, "running": True,
                "pid": pid, "port": port, "error": str(e),
            }))
        else:
            click.echo(f"Daemon: running (PID {pid}, port {port})")
            click.echo(f"Status: unavailable ({e})")
        return

    # Try to find this repo's name in active repos
    active_repos = health_data.get("active_repos", [])

    # Try querying per-repo status
    status_data: dict = {}
    for name in active_repos:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/repos/{name}/status", timeout=5.0)
            status_data = resp.json()
            break
        except (ImportError, OSError, ValueError, KeyError):  # noqa: BLE001
            log.debug("repo_status_query_failed", exc_info=True)
            continue

    if as_json:
        click.echo(json.dumps({
            "initialized": True,
            "running": True,
            "pid": pid,
            "port": port,
            "active_repos": active_repos,
            **status_data,
        }))
    else:
        click.echo(f"Daemon: running (PID {pid}, port {port})")
        click.echo(f"Repository: {repo_root}")
        click.echo(f"Active repos: {', '.join(active_repos) if active_repos else 'none'}")

        if status_data:
            indexer = status_data.get("indexer", {})
            click.echo(f"Indexer: {indexer.get('state', 'unknown')}")
            if indexer.get("queue_size", 0) > 0:
                click.echo(f"  Queue: {indexer['queue_size']} pending")

            worktrees = status_data.get("worktrees", {})
            for wt_name, wt_info in worktrees.items():
                stale = wt_info.get("stale", False)
                click.echo(f"  Worktree {wt_name}: {'stale' if stale else 'fresh'}")
