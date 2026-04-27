"""recon down command - stop the CodeRecon daemon."""

from __future__ import annotations

import time
from pathlib import Path

import click

from coderecon.daemon.global_lifecycle import (
    is_global_server_running,
    read_global_server_info,
    stop_global_daemon,
)

@click.command()
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
def down_command(path: Path | None) -> None:
    """Stop the CodeRecon daemon.

    Stops the global daemon process. If PATH is specified it is ignored
    (the daemon is global, not per-repo).
    """
    if not is_global_server_running():
        click.echo("Daemon is not running.")
        return

    info = read_global_server_info()
    if info is None:
        click.echo("Daemon is not running.")
        return

    pid, port = info
    click.echo(f"Stopping daemon (PID {pid}, port {port})...")

    if not stop_global_daemon():
        click.echo("Failed to send stop signal.", err=True)
        raise SystemExit(1)

    # Wait for process to exit (up to 5 seconds)
    for _ in range(50):
        if not is_global_server_running():
            click.echo("Daemon stopped.")
            return
        time.sleep(0.1)

    click.echo("Daemon did not stop within 5 seconds.", err=True)
    raise SystemExit(1)
