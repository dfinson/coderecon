"""Collect retrieval signals — CLI adapter for collect_signals.py."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import click
import httpx


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


def _find_clone_dir(clones_dir: Path, repo_id: str) -> Path | None:
    """Find the clone directory for a repo ID across all set dirs."""
    for set_dir in sorted(clones_dir.iterdir()):
        if not set_dir.is_dir():
            continue
        candidate = set_dir / repo_id
        if candidate.is_dir():
            return candidate
    return None


def _parse_port(mcp_url: str) -> int:
    """Extract the port number from the MCP URL."""
    parsed = urlparse(mcp_url)
    return parsed.port or 7654


def _start_cpl_server(clone_dir: Path, port: int = 7654) -> subprocess.Popen:
    """Start ``cpl up --dev-mode`` as a background process."""
    return subprocess.Popen(
        ["cpl", "up", "--dev-mode", "--port", str(port), str(clone_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_server(
    mcp_url: str, proc: subprocess.Popen, timeout: int = 60,
) -> bool:
    """Poll until the MCP endpoint accepts connections or *timeout* expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = (proc.stderr.read() or b"").decode(errors="replace").strip()
            click.echo(f"  cpl server exited early (code {proc.returncode})")
            if stderr:
                click.echo(f"  stderr: {stderr[:500]}")
            return False
        time.sleep(15)
        try:
            r = httpx.post(
                mcp_url,
                json={
                    "jsonrpc": "2.0",
                    "id": "health",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "cpl-health-check", "version": "1.0"},
                    },
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                timeout=5,
            )
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            elapsed = timeout - (deadline - time.monotonic())
            click.echo(f"  Waiting for server... ({elapsed:.0f}s / {timeout}s)")
    return False


def _stop_cpl_server(clone_dir: Path, proc: subprocess.Popen) -> None:
    """Stop the cpl daemon and clean up the background process."""
    subprocess.run(
        ["cpl", "down", str(clone_dir)],
        capture_output=True,
        check=False,
    )
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def run_collect(
    data_dir: Path,
    clones_dir: Path,
    repo_set: str = "all",
    repo: str | None = None,
    mcp_url: str = "http://127.0.0.1:7654/mcp",
    verbose: bool = False,
) -> None:
    """Collect retrieval signals for repos with ground truth.

    For each repo, starts a ``cpl up --dev-mode`` server, collects
    signals via MCP, then tears the server down before moving on.
    """
    from cpl_lab.collect_signals import collect_signals

    repo_ids = _iter_repos(data_dir, clones_dir, repo_set, repo)
    if not repo_ids:
        click.echo("No repos with ground truth found for signal collection.")
        return

    port = _parse_port(mcp_url)

    click.echo(f"Collecting signals for {len(repo_ids)} repos...")
    ok = failed = 0

    for rid in repo_ids:
        click.echo(f"\n=== {rid} ===")

        clone_dir = _find_clone_dir(clones_dir, rid)
        if clone_dir is None:
            click.echo(f"  ERROR: clone directory not found for {rid}", err=True)
            failed += 1
            continue

        # Start the cpl server for this repo
        click.echo(f"  Starting cpl server (port {port})...")
        proc = _start_cpl_server(clone_dir, port=port)
        try:
            if not _wait_for_server(mcp_url, proc):
                click.echo("  ERROR: cpl server failed to start within 60s", err=True)
                failed += 1
                continue

            click.echo("  Server ready, collecting signals...")
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
        finally:
            click.echo("  Stopping cpl server...")
            _stop_cpl_server(clone_dir, proc)

    click.echo(f"\nDone: {ok} collected, {failed} failed")
