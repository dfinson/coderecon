"""recon restart command - stop and re-launch the CodeRecon daemon."""

from __future__ import annotations

import time

import click

from coderecon.daemon.global_lifecycle import (
    is_global_server_running,
    read_global_server_info,
    stop_global_daemon,
)


@click.command()
@click.option(
    "--port",
    "-p",
    type=int,
    help="Server port.",
)
@click.option(
    "--dev-mode",
    is_flag=True,
    help="Enable development tools.",
)
@click.pass_context
def restart_command(ctx: click.Context, port: int | None, dev_mode: bool) -> None:
    """Restart the CodeRecon daemon (stop then start).

    Stops the running daemon (if any), then starts a fresh instance.
    Equivalent to ``recon down && recon up``.
    """
    from coderecon.cli.up import up_command

    # ── Stop phase ──
    if is_global_server_running():
        info = read_global_server_info()
        if info:
            pid, old_port = info
            click.echo(f"Stopping daemon (PID {pid}, port {old_port})...")

            if not stop_global_daemon():
                click.echo("Failed to send stop signal.", err=True)
                raise SystemExit(1)

            for _ in range(50):
                if not is_global_server_running():
                    click.echo("Daemon stopped.")
                    break
                time.sleep(0.1)
            else:
                click.echo("Daemon did not stop within 5 seconds.", err=True)
                raise SystemExit(1)
    else:
        click.echo("No running daemon found — starting fresh.")

    # ── Start phase — delegate to `recon up` ──
    ctx.invoke(up_command, port=port, dev_mode=dev_mode)
