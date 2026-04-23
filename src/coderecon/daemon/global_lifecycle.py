"""Global daemon lifecycle — start/stop/status for the multi-repo server."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path

import structlog
import uvicorn

from coderecon.catalog.db import CatalogDB, _default_coderecon_home
from coderecon.catalog.registry import CatalogRegistry

log = structlog.get_logger(__name__)

DEFAULT_PORT = 7654
PID_FILE = "daemon.pid"
PORT_FILE = "daemon.port"


def _coderecon_dir() -> Path:
    return _default_coderecon_home()


def write_global_pid(home: Path, port: int) -> None:
    """Write PID and port files for global daemon discovery."""
    (home / PID_FILE).write_text(str(os.getpid()))
    (home / PORT_FILE).write_text(str(port))


def remove_global_pid(home: Path) -> None:
    """Remove PID and port files on shutdown."""
    for name in (PID_FILE, PORT_FILE):
        with contextlib.suppress(FileNotFoundError):
            (home / name).unlink()


def read_global_server_info(home: Path | None = None) -> tuple[int, int] | None:
    """Read global daemon PID and port. Returns (pid, port) or None."""
    home = home or _coderecon_dir()
    try:
        pid = int((home / PID_FILE).read_text().strip())
        port = int((home / PORT_FILE).read_text().strip())
        return (pid, port)
    except (FileNotFoundError, ValueError):
        return None


def is_global_server_running(home: Path | None = None) -> bool:
    """Check if the global daemon is running."""
    home = home or _coderecon_dir()
    info = read_global_server_info(home)
    if info is None:
        return False

    pid, _ = info
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        remove_global_pid(home)
        return False


def stop_global_daemon(home: Path | None = None) -> bool:
    """Stop the global daemon by sending SIGTERM. Returns True if stopped."""
    home = home or _coderecon_dir()
    info = read_global_server_info(home)
    if info is None:
        return False

    pid, _ = info
    try:
        os.kill(pid, signal.SIGTERM)
        log.info("global_daemon_stop_signal_sent", pid=pid)
        return True
    except (OSError, ProcessLookupError):
        remove_global_pid(home)
        return False


async def run_global_server(
    *,
    port: int = DEFAULT_PORT,
    dev_mode: bool = False,
) -> None:
    """Run the global multi-repo daemon until shutdown signal.

    Activates all repos currently registered in the catalog on startup.
    Use ``recon register [PATH]`` (or POST /catalog/register) to add repos
    before or after the daemon starts.

    Args:
        port: Port to bind to.
        dev_mode: Enable dev mode for MCP servers.
    """
    from coderecon.daemon.global_app import GlobalDaemon

    home = _coderecon_dir()
    home.mkdir(parents=True, exist_ok=True)

    catalog = CatalogDB(home)
    registry = CatalogRegistry(catalog)
    daemon = GlobalDaemon(registry)

    # No eager repo activation — repos are lazily activated on first MCP
    # request via _DynamicMcpRouter → lazy_activate_repo().  This avoids
    # loading 95+ SQLite indices and tantivy stores at boot when only a
    # handful of repos will actually receive MCP connections.
    log.info("repos_registered", count=len(registry.list_repos()))

    # Startup scans are a no-op with zero active slots, but harmless to call.
    await daemon.queue_startup_scans()

    # Start idle-eviction loop for non-main worktrees.
    from coderecon.config.loader import load_config as _load_cfg

    _cfg = _load_cfg(Path.cwd())
    daemon.start_eviction_loop(_cfg.server.worktree_idle_timeout_sec)

    # Build global Starlette app
    app = daemon.build_app(dev_mode=dev_mode)

    log.info(
        "global_daemon_starting",
        port=port,
        active_repos=daemon.slot_names,
    )

    # Configure uvicorn — disable its built-in signal handler installation so
    # our handlers below are not overwritten when server.serve() starts.
    uvicorn_config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="critical",
        ws="none",
        timeout_graceful_shutdown=2,
    )

    class _Server(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            pass  # handled externally

    server = _Server(uvicorn_config)

    # Write PID before registering signals so recon down can find us immediately
    write_global_pid(home, port)

    # Signal handling — registered after uvicorn.Server is constructed so
    # nothing overwrites them.  First signal → graceful exit; second → force.
    loop = asyncio.get_event_loop()
    shutdown_count = 0

    def signal_handler() -> None:
        nonlocal shutdown_count
        shutdown_count += 1
        server.should_exit = True
        if shutdown_count > 1:
            server.force_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await server.serve()
    finally:
        # Hard timeout on cleanup so recon down never waits more than ~4s total
        try:
            await asyncio.wait_for(daemon.stop_all(), timeout=3.0)
        except asyncio.TimeoutError:
            log.warning("daemon_stop_all_timeout")
        remove_global_pid(home)


async def run_global_server_stdio(
    *,
    dev_mode: bool = False,
) -> None:
    """Run the global daemon in stdio mode (child process of SDK).

    Reads NDJSON requests from stdin, writes responses + events to stdout.
    No HTTP server, no PID file — the SDK owns the process lifecycle.
    """
    from coderecon.daemon.global_app import GlobalDaemon
    from coderecon.daemon.stdio_transport import run_stdio_loop

    home = _coderecon_dir()
    home.mkdir(parents=True, exist_ok=True)

    catalog = CatalogDB(home)
    registry = CatalogRegistry(catalog)
    daemon = GlobalDaemon(registry)

    log.info("stdio_daemon_starting", repos=len(registry.list_repos()))

    await daemon.queue_startup_scans()

    from coderecon.config.loader import load_config as _load_cfg

    _cfg = _load_cfg(Path.cwd())
    daemon.start_eviction_loop(_cfg.server.worktree_idle_timeout_sec)

    try:
        await run_stdio_loop(daemon, registry)
    finally:
        try:
            await asyncio.wait_for(daemon.stop_all(), timeout=3.0)
        except asyncio.TimeoutError:
            log.warning("daemon_stop_all_timeout")
