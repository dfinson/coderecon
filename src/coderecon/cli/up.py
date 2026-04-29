"""recon up command - start the global daemon."""

from __future__ import annotations

import asyncio
import sys
from importlib.metadata import version

import click
import structlog

from coderecon.config.user_config import DEFAULT_PORT
from coderecon._core.progress import (
    animate_text,
    get_console,
)

LOGO = r"""
                    ++++++++++++++++++++++
                 *++++++++++++++++++++++++++*
               *+++++++*              *+++++++*
             *+++++++                    *++++++*
            ++++++*                        *++++++
          *+++++                              +++++*
         ++++++                                *+++++
         +++++             ++++                 +++++
         *****              +++++                ****
                              +++++
   *++++++++++++++++++++++     *++++ ++++++++++++++++++++++
                              ++++*
         ++++*              ++++*               *++++
         +++++             ++++                 +++++
         *+++++                                +++++*
           *+++++                            ++++++
            *++++++*                       +++++++
              ++++++++                  ++++++++
               *++++++++++          ++++++++++*
                  *+++++++++++++++++++++++++
                     *++++++++++++++++++*
"""

def _print_banner(host: str, port: int, *, animate: bool = True) -> None:
    ver = version("coderecon")
    console = get_console()

    if animate:
        animate_text(LOGO, delay=0.015)
    else:
        console.print(LOGO, highlight=False)

    banner_width = 64
    rule_line = "─" * banner_width
    base_url = f"http://{host}:{port}"

    console.print()
    console.print(rule_line, style="dim cyan", highlight=False)
    console.print(
        f"CodeRecon v{ver} · Ready".center(banner_width), style="bold cyan", highlight=False
    )
    console.print(rule_line, style="dim cyan", highlight=False)
    console.print()
    console.print(f"  Health:          {base_url}/health", highlight=False)
    console.print(f"  Catalog:         {base_url}/catalog", highlight=False)
    console.print()
    console.print(
        "  Use 'recon register [PATH]' to add a repository.",
        style="dim",
        highlight=False,
    )
    console.print()

@click.command()
@click.option(
    "--port", "-p", type=int, default=DEFAULT_PORT, show_default=True,
    help="Port to bind to.",
)
@click.option(
    "--stdio",
    is_flag=True,
    help="Start in stdio mode (child process of SDK). Mutually exclusive with --port.",
)
@click.option(
    "--dev-mode",
    is_flag=True,
    help="Enable development tools (recon_raw_signals endpoint)",
)
def up_command(port: int, stdio: bool, dev_mode: bool) -> None:
    """Start the global CodeRecon daemon.

    Activates all repositories already registered in the catalog.
    Use ``recon register [PATH]`` to add a repository before or after
    the daemon starts.
    """
    from coderecon.adapters.catalog.db import _default_coderecon_home
    from coderecon.config.models import LoggingConfig, LogOutputConfig
    from coderecon._core.logging import configure_logging

    if stdio:
        # Stdio mode — NDJSON over stdin/stdout, no banner, no PID file.
        from coderecon.daemon.global_lifecycle import run_global_server_stdio

        home = _default_coderecon_home()
        from datetime import datetime
        from uuid import uuid4

        now = datetime.now()
        server_run_id = uuid4().hex[:6]
        log_dir = home / "logs" / now.strftime("%Y-%m-%d")
        log_file = log_dir / f"{now.strftime('%H%M%S')}-stdio-{server_run_id}.log"

        # Log to file only — stdout is the wire protocol
        configure_logging(
            config=LoggingConfig(
                level="DEBUG",
                outputs=[
                    LogOutputConfig(destination=str(log_file), format="json", level="DEBUG"),
                ],
            ),
        )

        try:
            asyncio.run(run_global_server_stdio(dev_mode=dev_mode))
        except KeyboardInterrupt:
            structlog.get_logger().debug("keyboard_interrupt_during_stdio_server", exc_info=True)
            pass
        return

    from coderecon.daemon.global_lifecycle import (
        is_global_server_running,
        read_global_server_info,
        run_global_server,
    )

    if is_global_server_running():
        info = read_global_server_info()
        if info:
            pid, server_port = info
            click.echo(f"Daemon already running (PID {pid}, port {server_port})")
            click.echo(f"  Health:  http://127.0.0.1:{server_port}/health")
            click.echo(f"  Catalog: http://127.0.0.1:{server_port}/catalog")
            click.echo("Use 'recon register [PATH]' to add a repository.")
        return

    # Log to ~/.coderecon/logs/
    from datetime import datetime
    from uuid import uuid4

    home = _default_coderecon_home()
    now = datetime.now()
    server_run_id = uuid4().hex[:6]
    log_dir = home / "logs" / now.strftime("%Y-%m-%d")
    log_file = log_dir / f"{now.strftime('%H%M%S')}-{server_run_id}.log"

    configure_logging(
        config=LoggingConfig(
            level="DEBUG",
            outputs=[
                LogOutputConfig(destination="stderr", format="console", level="INFO"),
                LogOutputConfig(destination=str(log_file), format="json", level="DEBUG"),
            ],
        ),
    )

    _print_banner("127.0.0.1", port)

    _original_unraisablehook = sys.unraisablehook

    def _suppress_event_loop_closed(unraisable: object) -> None:
        if getattr(unraisable, "exc_type", None) is RuntimeError and "Event loop is closed" in str(
            getattr(unraisable, "exc_value", "")
        ):
            return
        _original_unraisablehook(unraisable)  # type: ignore[arg-type]

    sys.unraisablehook = _suppress_event_loop_closed

    try:
        asyncio.run(run_global_server(port=port, dev_mode=dev_mode))
    except KeyboardInterrupt:
        click.echo("\nStopped")
    finally:
        sys.unraisablehook = _original_unraisablehook
