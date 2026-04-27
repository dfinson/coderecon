"""File watcher using watchfiles for async filesystem monitoring.

Design:
- Python walks the repo tree respecting IgnoreChecker pruning tiers
- Builds an explicit list of directories to watch
- Passes them to awatch with recursive=False (one inotify watch per dir)
- Reacts immediately to new directory creation by restarting awatch
- Falls back to mtime polling for cross-filesystem (WSL /mnt/*)

Improved logging (Issues #4, #6):
- Change detection logs summarize by file type with grammatical correctness
- reconignore changes show pattern diff and explain consequence
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from watchfiles import Change, awatch

from coderecon.daemon.watcher_utils import (
    collect_watch_dirs as _collect_watch_dirs,
    is_cross_filesystem as _is_cross_filesystem,
    summarize_changes_by_type as _summarize_changes_by_type,
)
from coderecon.index._internal.ignore import IgnoreChecker

log = structlog.get_logger(__name__)

# Debouncing configuration
DEBOUNCE_WINDOW_SEC = 0.5  # Sliding window for batching rapid changes
MAX_DEBOUNCE_WAIT_SEC = 2.0  # Maximum wait before forcing flush


@dataclass
class FileWatcher:
    """
    Async file watcher with sliding-window debouncing.

    Design:
    - Python walks repo tree to build explicit directory list
    - Uses watchfiles with recursive=False (one inotify watch per dir)
    - Reacts immediately to new directory creation by restarting awatch
    - Falls back to mtime polling for cross-filesystem (WSL /mnt/*)
    - Implements sliding-window debounce to batch rapid changes
    - Filters changes through IgnoreChecker before emitting
    - Detects .reconignore changes and reloads filter
    - Notifies callback with batched path changes

    Debouncing (Solution A + B combined):
    - Solution A: Sliding window debounce in watcher itself
    - Solution B: BackgroundIndexer also coalesces (defense in depth)
    - Changes are buffered until DEBOUNCE_WINDOW_SEC of quiet time
    - MAX_DEBOUNCE_WAIT_SEC caps maximum delay for rapid fire changes
    """

    repo_root: Path
    on_change: Callable[[list[Path]], None]
    poll_interval: float = 1.0  # Seconds between mtime polls (cross-filesystem)
    debounce_window: float = DEBOUNCE_WINDOW_SEC
    max_debounce_wait: float = MAX_DEBOUNCE_WAIT_SEC

    _ignore_checker: IgnoreChecker = field(init=False)
    _watch_task: asyncio.Task[None] | None = field(default=None, init=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _is_cross_fs: bool = field(init=False)
    # Debouncing state
    _pending_changes: set[Path] = field(default_factory=set, init=False)
    _last_change_time: float = field(default=0.0, init=False)
    _first_change_time: float = field(default=0.0, init=False)
    _debounce_task: asyncio.Task[None] | None = field(default=None, init=False)
    # Track previous reconignore content for diff (Issue #6)
    _last_reconignore_content: str | None = field(default=None, init=False)
    _dir_scan_task: asyncio.Task[None] | None = field(default=None, init=False)
    # Watched directory set for non-recursive mode
    _watched_dirs: set[Path] = field(default_factory=set, init=False)
    # Whether this watcher degraded to poll mode due to inotify capacity.
    _degraded_to_poll: bool = field(default=False, init=False)
    @property
    def watch_count(self) -> int:
        """Number of directories currently being watched via inotify."""
        return len(self._watched_dirs)
    @staticmethod
    def estimate_watch_count(repo_root: Path) -> int:
        """Estimate how many inotify watches a repo would need.

        Walks the tree using the same pruning logic as ``_collect_watch_dirs``
        but only counts — no watches are created.
        """
        ignore_checker = IgnoreChecker(repo_root, respect_gitignore=False)
        return len(_collect_watch_dirs(repo_root, ignore_checker))
    def __post_init__(self) -> None:
        """Initialize ignore checker and detect cross-filesystem."""
        self._ignore_checker = IgnoreChecker(self.repo_root, respect_gitignore=False)
        self._is_cross_fs = _is_cross_filesystem(self.repo_root)
        # Capture initial reconignore content for diff
        reconignore_path = self.repo_root / ".recon" / ".reconignore"
        if reconignore_path.exists():
            with contextlib.suppress(OSError):
                self._last_reconignore_content = reconignore_path.read_text()

    async def start(self) -> None:
        """Start watching for file changes."""
        if self._watch_task is not None:
            return

        self._stop_event.clear()
        if self._is_cross_fs:
            self._watch_task = asyncio.create_task(self._poll_loop())
            log.info(
                "file_watcher_started",
                repo_root=str(self.repo_root),
                mode="polling",
                interval=self.poll_interval,
                debounce_window=self.debounce_window,
            )
        else:
            self._watch_task = asyncio.create_task(self._watch_loop())
            # Periodic safety-net scan for directory changes we might have missed
            self._dir_scan_task = asyncio.create_task(self._periodic_dir_scan())
            log.info(
                "file_watcher_started",
                repo_root=str(self.repo_root),
                mode="native_nonrecursive",
                debounce_window=self.debounce_window,
            )

    async def stop(self) -> None:
        """Stop watching for file changes."""
        self._stop_event.set()

        # Cancel debounce task if pending
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._debounce_task
            self._debounce_task = None

        # Cancel dir scan task
        if self._dir_scan_task is not None and not self._dir_scan_task.done():
            self._dir_scan_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dir_scan_task
            self._dir_scan_task = None

        # Flush any pending changes before stopping
        if self._pending_changes:
            self._flush_pending()

        if self._watch_task is not None:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(self._watch_task, timeout=2.0)
            self._watch_task = None

        log.info("file_watcher_stopped")
    def _queue_change(self, path: Path) -> None:
        """Queue a change for debounced delivery."""
        now = time.monotonic()

        if not self._pending_changes:
            self._first_change_time = now

        self._pending_changes.add(path)
        self._last_change_time = now
    def _should_flush(self) -> bool:
        """Check if we should flush pending changes."""
        if not self._pending_changes:
            return False

        now = time.monotonic()
        time_since_last = now - self._last_change_time
        time_since_first = now - self._first_change_time

        # Flush if quiet window elapsed OR max wait exceeded
        return time_since_last >= self.debounce_window or time_since_first >= self.max_debounce_wait
    def _flush_pending(self) -> None:
        """Flush pending changes to callback."""
        if not self._pending_changes:
            return

        paths = list(self._pending_changes)
        self._pending_changes.clear()
        self._first_change_time = 0.0
        self._last_change_time = 0.0

        # Log with human-readable summary (Issue #4)
        summary = _summarize_changes_by_type(paths)
        log.info("changes_detected", count=len(paths), summary=summary)

        self.on_change(paths)

    async def _debounce_flush_loop(self) -> None:
        """Background task that flushes when debounce window elapses."""
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(0.1)  # Check every 100ms

                if self._should_flush():
                    self._flush_pending()
        except asyncio.CancelledError:
            structlog.get_logger().debug("debounce_flush_loop_cancelled", exc_info=True)
            pass

    async def _watch_loop(self) -> None:
        """Main watch loop using watchfiles with non-recursive inotify.

        Python builds the complete directory list, awatch gets recursive=False.
        This prevents notify-rs from recursively traversing into prunable dirs
        (node_modules, .venv, etc.) which causes inotify setup failures and
        silent fallback to CPU-intensive PollWatcher.

        When new directories are created, we detect them via inotify events
        and restart awatch to include them.
        """
        # Start debounce flush task
        self._debounce_task = asyncio.create_task(self._debounce_flush_loop())

        try:
            while not self._stop_event.is_set():
                # Build directory list using Python walk with pruning
                watch_dirs = _collect_watch_dirs(self.repo_root, self._ignore_checker)
                self._watched_dirs = set(watch_dirs)

                if not watch_dirs:
                    log.warning("no_watchable_dirs", repo_root=str(self.repo_root))
                    return

                log.info(
                    "watch_dirs_collected",
                    count=len(watch_dirs),
                    repo_root=str(self.repo_root),
                )

                try:
                    async for changes in awatch(
                        *watch_dirs,
                        recursive=False,
                        force_polling=False,  # Override WSL auto-detect (watchfiles#187)
                        step=500,  # Check Rust side every 500ms
                        rust_timeout=10_000,
                        stop_event=self._stop_event,
                        ignore_permission_denied=True,
                    ):
                        needs_restart = await self._handle_changes(changes)
                        if needs_restart:
                            log.info("watcher_restart_requested", reason="new_directories")
                            break  # Break inner loop to re-collect dirs
                except asyncio.CancelledError:
                    raise
                except OSError as e:
                    if e.errno == 28:  # ENOSPC — out of inotify watches
                        log.warning(
                            "inotify_enospc_fallback",
                            repo_root=str(self.repo_root),
                            watch_dirs=len(watch_dirs),
                        )
                        self._degraded_to_poll = True
                        # Cancel debounce task before switching loops
                        if self._debounce_task and not self._debounce_task.done():
                            self._debounce_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await self._debounce_task
                        self._watch_task = asyncio.create_task(self._poll_loop())
                        return  # Exit inotify loop; poll loop takes over
                    raise
                except Exception as e:
                    if self._stop_event.is_set():
                        return
                    log.error("watcher_error", error=str(e))
                    # Brief backoff before retry
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            structlog.get_logger().debug("watch_loop_cancelled", exc_info=True)
            pass
        finally:
            if self._debounce_task:
                self._debounce_task.cancel()

    async def _periodic_dir_scan(self) -> None:
        """Periodic safety-net scan for directory changes.

        Primary detection is via inotify events in _handle_changes.
        This scan catches edge cases: directories created during watcher restart,
        symlink changes, or any events that slipped through.
        """
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(30.0)
                current_dirs = set(_collect_watch_dirs(self.repo_root, self._ignore_checker))
                if current_dirs != self._watched_dirs:
                    new_dirs = current_dirs - self._watched_dirs
                    removed_dirs = self._watched_dirs - current_dirs
                    log.info(
                        "dir_scan_drift_detected",
                        new_count=len(new_dirs),
                        removed_count=len(removed_dirs),
                        new_sample=[
                            str(d.relative_to(self.repo_root)) for d in sorted(new_dirs)[:5]
                        ],
                    )
                    # Signal the watch loop to restart by cancelling it.
                    # The outer while loop in _watch_loop will re-collect dirs.
                    if self._watch_task and not self._watch_task.done():
                        self._watch_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await self._watch_task
                        self._watch_task = asyncio.create_task(self._watch_loop())
        except asyncio.CancelledError:
            structlog.get_logger().debug("periodic_dir_scan_cancelled", exc_info=True)
            pass

    async def _poll_loop(self) -> None:
        """Poll loop using mtime checks (for cross-filesystem where inotify fails).

        Uses os.walk with pruning rather than git status,
        since gitignored files may still be indexed if not in .reconignore.

        Implements sliding-window debounce for burst handling.
        """
        # Track mtimes for all non-reconignored files
        mtimes: dict[Path, float] = {}

        # Initial scan
        mtimes = self._scan_mtimes()

        # Start debounce flush task
        self._debounce_task = asyncio.create_task(self._debounce_flush_loop())

        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(self.poll_interval)

                try:
                    current_mtimes = self._scan_mtimes()

                    # Find changed files
                    for path, mtime in current_mtimes.items():
                        old_mtime = mtimes.get(path)
                        if old_mtime is None or mtime > old_mtime:
                            rel_path = path.relative_to(self.repo_root)
                            # Filter: exclude .git, check reconignore
                            if ".git" not in rel_path.parts and (
                                rel_path.name == ".reconignore"
                                or not self._ignore_checker.should_ignore(self.repo_root / rel_path)
                            ):
                                self._queue_change(rel_path)

                    # Find deleted files
                    for path in mtimes:
                        if path not in current_mtimes:
                            rel_path = path.relative_to(self.repo_root)
                            # Filter: exclude .git, check reconignore
                            if ".git" not in rel_path.parts and (
                                rel_path.name == ".reconignore"
                                or not self._ignore_checker.should_ignore(self.repo_root / rel_path)
                            ):
                                self._queue_change(rel_path)

                    mtimes = current_mtimes

                except Exception as e:
                    log.error("poll_error", error=str(e), exc_info=True)
        finally:
            if self._debounce_task:
                self._debounce_task.cancel()
    def _scan_mtimes(self) -> dict[Path, float]:
        """Scan filesystem for file mtimes, respecting prunable dirs."""
        mtimes: dict[Path, float] = {}
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            # Prune using IgnoreChecker (respects tiers + .reconignore negation)
            dirnames[:] = [d for d in dirnames if not self._ignore_checker.should_prune_dir(d)]

            for filename in filenames:
                file_path = Path(dirpath) / filename
                with contextlib.suppress(OSError):
                    mtimes[file_path] = file_path.stat().st_mtime

        return mtimes
    def _handle_reconignore_change(self, rel_path: Path) -> None:
        """Handle .reconignore change with detailed logging (Issue #6)."""
        reconignore_path = self.repo_root / rel_path

        # Read new content
        new_content: str | None = None
        if reconignore_path.exists():
            with contextlib.suppress(OSError):
                new_content = reconignore_path.read_text()

        # Compute diff stats
        old_patterns = {
            line.strip()
            for line in (self._last_reconignore_content or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        new_patterns = {
            line.strip()
            for line in (new_content or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }

        added = new_patterns - old_patterns
        removed = old_patterns - new_patterns

        # Log with diff summary (Issue #6 Option B)
        diff_parts: list[str] = []
        if added:
            suffix = "s" if len(added) != 1 else ""
            diff_parts.append(f"+{len(added)} pattern{suffix}")
        if removed:
            suffix = "s" if len(removed) != 1 else ""
            diff_parts.append(f"-{len(removed)} pattern{suffix}")
        diff_summary = ", ".join(diff_parts) if diff_parts else "no changes"

        log.info(
            "reconignore_changed",
            path=str(rel_path),
            diff=diff_summary,
            added_patterns=list(added)[:5] if added else None,  # Sample up to 5
            removed_patterns=list(removed)[:5] if removed else None,
        )

        # Log consequence (Issue #6 Option A)
        log.info(
            "full_reindex_triggered",
            reason="ignore_patterns_changed",
            patterns_added=len(added),
            patterns_removed=len(removed),
        )

        # Update cached content
        self._last_reconignore_content = new_content

    async def _handle_changes(self, changes: set[tuple[Change, str]]) -> bool:
        """Process a batch of file changes (queue for debouncing).

        Returns True if a watcher restart is needed (new directories detected).
        """
        needs_restart = False

        for change_type, path_str in changes:
            path = Path(path_str)

            # Skip .git directory
            try:
                rel_path = path.relative_to(self.repo_root)
            except ValueError:
                structlog.get_logger().debug("path_not_relative_to_repo", path=path_str, exc_info=True)
                continue

            if ".git" in rel_path.parts:
                continue

            # Detect new directory creation — request watcher restart to add watch
            if change_type == Change.added and path.is_dir():
                if (
                    not self._ignore_checker.should_prune_dir(path.name)
                    and path not in self._watched_dirs
                ):
                    log.info(
                        "new_directory_detected",
                        path=str(rel_path),
                    )
                    needs_restart = True
                continue  # Directories themselves don't get queued as file changes

            # Check for .reconignore change
            if rel_path.name == ".reconignore":
                self._handle_reconignore_change(rel_path)
                self._queue_change(rel_path)
                continue

            # Filter through .reconignore
            if self._ignore_checker.should_ignore(self.repo_root / rel_path):
                log.debug("path_ignored", path=str(rel_path))
                continue

            self._queue_change(rel_path)
            log.debug(
                "path_queued",
                path=str(rel_path),
                change_type=change_type.name,
            )

        return needs_restart
