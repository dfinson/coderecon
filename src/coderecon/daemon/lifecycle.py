"""Daemon lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import uvicorn

from coderecon.config.models import CodeReconConfig, IndexerConfig, ServerConfig, TimeoutsConfig
from coderecon.daemon.concurrency import FreshnessGate
from coderecon.daemon.indexer import BackgroundIndexer
from coderecon.daemon.watcher import FileWatcher
from coderecon.adapters.files.ops import atomic_write_text

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)

# PID file location relative to .recon/
PID_FILE = "daemon.pid"
PORT_FILE = "daemon.port"

@dataclass
class ServerController:
    """
    Orchestrates daemon components for single-repo mode.

    Components:
    - IndexCoordinatorEngine: Database and search operations
    - BackgroundIndexer: Thread pool for CPU-bound indexing
    - FileWatcher: Async filesystem monitoring
    - FreshnessGate: Per-worktree staleness tracking
    """

    repo_root: Path
    coordinator: IndexCoordinatorEngine
    server_config: ServerConfig
    timeouts_config: TimeoutsConfig = field(default_factory=TimeoutsConfig)
    indexer_config: IndexerConfig = field(default_factory=IndexerConfig)
    gate: FreshnessGate = field(default_factory=FreshnessGate)

    indexer: BackgroundIndexer = field(init=False)
    watcher: FileWatcher = field(init=False)
    _shutdown_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def __post_init__(self) -> None:
        """Initialize components."""
        # Create indexer with config and gate
        self.indexer = BackgroundIndexer(
            coordinator=self.coordinator,
            gate=self.gate,
            config=self.indexer_config,
        )

        # Create watcher — routes changes through indexer tagged as "main" worktree
        self.watcher = FileWatcher(
            repo_root=self.repo_root,
            on_change=lambda paths: self.indexer.queue_paths("main", paths),
            poll_interval=self.server_config.poll_interval_sec,
        )

    async def start(self) -> None:
        """Start all daemon components."""
        log.info("server starting", repo_root=str(self.repo_root))

        # Start indexer thread pool
        self.indexer.start()

        # Start file watcher
        await self.watcher.start()

        base_url = f"http://{self.server_config.host}:{self.server_config.port}"
        log.info("server started")
        log.info("endpoint", name="mcp", url=f"{base_url}/mcp")
        log.info("endpoint", name="health", url=f"{base_url}/health")
        log.info("endpoint", name="status", url=f"{base_url}/status")

    async def stop(self) -> None:
        """Stop all daemon components gracefully."""
        log.info("server stopping")

        # Stop with timeout to prevent hanging
        try:
            async with asyncio.timeout(self.timeouts_config.server_stop_sec):
                # Stop watcher first (no new events)
                await self.watcher.stop()

                # Stop indexer (complete pending work)
                await self.indexer.stop()
        except TimeoutError:
            log.warning(
                "server_stop_timeout",
                message=f"Shutdown timed out after {self.timeouts_config.server_stop_sec}s",
            )

        # Signal shutdown complete
        self._shutdown_event.set()

        log.info("server stopped")

    def wait_for_shutdown(self) -> asyncio.Event:
        """Get the shutdown event for external coordination."""
        return self._shutdown_event

def write_pid_file(coderecon_dir: Path, port: int) -> None:
    """Write PID and port files for daemon discovery."""
    import os

    pid_path = coderecon_dir / PID_FILE
    port_path = coderecon_dir / PORT_FILE

    atomic_write_text(pid_path, str(os.getpid()))
    atomic_write_text(port_path, str(port))

    log.debug("pid_file_written", pid_path=str(pid_path), port=port)

def remove_pid_file(coderecon_dir: Path) -> None:
    """Remove PID and port files on shutdown."""
    pid_path = coderecon_dir / PID_FILE
    port_path = coderecon_dir / PORT_FILE

    for path in (pid_path, port_path):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

def read_server_info(coderecon_dir: Path) -> tuple[int, int] | None:
    """Read daemon PID and port from files. Returns (pid, port) or None."""
    pid_path = coderecon_dir / PID_FILE
    port_path = coderecon_dir / PORT_FILE

    try:
        pid = int(pid_path.read_text().strip())
        port = int(port_path.read_text().strip())
        return (pid, port)
    except (FileNotFoundError, ValueError):
        log.debug("server_info_read_failed", exc_info=True)
        return None

def is_server_running(coderecon_dir: Path) -> bool:
    """Check if daemon is running by verifying PID file and process."""
    import os

    info = read_server_info(coderecon_dir)
    if info is None:
        return False

    pid, _ = info

    # Check if process exists
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        # Process doesn't exist - clean up stale files
        remove_pid_file(coderecon_dir)
        return False

async def run_server(
    repo_root: Path,
    coordinator: IndexCoordinatorEngine,
    config: CodeReconConfig,
    *,
    dev_mode: bool = False,
) -> None:
    """Run the daemon until shutdown signal."""
    # Ensure index is up-to-date (silent - this is internal housekeeping)
    # Run in background after server starts to avoid blocking startup
    import asyncio

    from coderecon.daemon.app import create_app

    async def _bg_reindex() -> None:
        try:
            await coordinator.reindex_full()
        except (OSError, RuntimeError, ValueError):
            log.debug("bg_reindex_failed", exc_info=True)
            # Non-fatal; server still usable with stale index

    asyncio.get_event_loop().call_soon(lambda: asyncio.ensure_future(_bg_reindex()))

    # Print banner with logo
    from coderecon.cli.up import _print_banner

    _print_banner(config.server.host, config.server.port, repo_root)

    controller = ServerController(
        repo_root=repo_root,
        coordinator=coordinator,
        server_config=config.server,
        timeouts_config=config.timeouts,
        indexer_config=config.indexer,
    )

    app = create_app(controller, repo_root, coordinator, dev_mode=dev_mode)

    # Configure uvicorn with graceful shutdown
    uvicorn_config = uvicorn.Config(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="critical",  # Suppress ASGI warnings on shutdown
        ws="none",  # Disable websockets - we use SSE for MCP
        timeout_graceful_shutdown=2,  # Give connections 2s to close
    )
    server = uvicorn.Server(uvicorn_config)

    # Write PID file
    coderecon_dir = repo_root / ".recon"
    write_pid_file(coderecon_dir, config.server.port)

    # Setup signal handlers with force exit on second signal
    loop = asyncio.get_event_loop()
    shutdown_count = 0
    force_exit_task: asyncio.Task[None] | None = None

    async def force_exit_after_timeout() -> None:
        """Force exit if graceful shutdown takes too long."""
        await asyncio.sleep(config.timeouts.force_exit_sec)
        log.info("forcing_exit_after_timeout")
        server.force_exit = True

    def signal_handler() -> None:
        nonlocal shutdown_count, force_exit_task
        shutdown_count += 1
        log.info("shutdown_signal_received", count=shutdown_count)
        server.should_exit = True
        if shutdown_count == 1:
            # Schedule force exit after timeout
            force_exit_task = loop.create_task(force_exit_after_timeout())
        else:
            # Second signal - force immediate exit
            server.force_exit = True
            if force_exit_task:
                force_exit_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await controller.start()
        await server.serve()
    finally:
        await controller.stop()
        remove_pid_file(coderecon_dir)

def stop_daemon(coderecon_dir: Path) -> bool:
    """Stop a running daemon by sending SIGTERM. Returns True if stopped."""
    import os

    info = read_server_info(coderecon_dir)
    if info is None:
        return False

    pid, _ = info

    try:
        os.kill(pid, signal.SIGTERM)
        log.info("daemon_stop_signal_sent", pid=pid)
        return True
    except (OSError, ProcessLookupError):
        remove_pid_file(coderecon_dir)
        return False
