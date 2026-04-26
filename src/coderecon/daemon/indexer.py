"""Background indexer using thread pool for CPU-bound work.

Worktree-aware: changes are tagged with the source worktree so that
FreshnessGate can mark only the affected worktree as stale/fresh.

Improved logging:
- Uses spinner with log suppression to prevent line collision (Issue #5)
- Grammatically correct result summaries (Issue #4)
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from coderecon.config.models import IndexerConfig
from coderecon.core.formatting import pluralize
from coderecon.core.progress import spinner, status

if TYPE_CHECKING:
    from coderecon.daemon.concurrency import FreshnessGate
    from coderecon.index.ops import IndexCoordinatorEngine, IndexStats

log = structlog.get_logger(__name__)


class IndexerState(Enum):
    """Background indexer state."""

    IDLE = "idle"
    INDEXING = "indexing"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class IndexerStatus:
    """Current indexer status."""

    state: IndexerState
    queue_size: int
    last_stats: IndexStats | None = None
    last_error: str | None = None


@dataclass
class BackgroundIndexer:
    """
    Non-blocking indexer using thread pool for CPU-bound work.

    Design:
    - HTTP server runs in main asyncio loop
    - Indexing work is submitted to ThreadPoolExecutor
    - Queue batches rapid changes with debouncing
    - IndexCoordinatorEngine locks ensure thread safety
    - Changes are tagged with source worktree for per-worktree freshness
    """

    coordinator: IndexCoordinatorEngine
    gate: FreshnessGate
    config: IndexerConfig = field(default_factory=IndexerConfig)

    _state: IndexerState = field(default=IndexerState.IDLE, init=False)
    _executor: ThreadPoolExecutor | None = field(default=None, init=False)
    _pending: dict[str, set[Path]] = field(default_factory=dict, init=False)
    _pending_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _debounce_task: asyncio.Task[None] | None = field(default=None, init=False)
    _last_stats: IndexStats | None = field(default=None, init=False)
    _last_error: str | None = field(default=None, init=False)
    _on_complete_callbacks: list[Callable[[IndexStats, list[Path]], Awaitable[None]]] = field(default_factory=list, init=False)

    def start(self) -> None:
        """Start the background indexer."""
        if self._executor is not None:
            return
        self._executor = ThreadPoolExecutor(
            max_workers=self.config.max_workers,
            thread_name_prefix="coderecon-indexer",
        )
        self._state = IndexerState.IDLE
        log.info("background_indexer_started", max_workers=self.config.max_workers)

    async def stop(self) -> None:
        """Stop the background indexer gracefully."""
        self._state = IndexerState.STOPPING

        # Cancel pending debounce
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._debounce_task

        # Shutdown executor with timeout to prevent hanging
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

        self._state = IndexerState.STOPPED
        log.info("background_indexer_stopped")

    def queue_paths(self, worktree: str, paths: list[Path]) -> None:
        """Queue paths for indexing with debouncing, tagged by worktree."""
        with self._pending_lock:
            bucket = self._pending.setdefault(worktree, set())
            bucket.update(paths)
            count = sum(len(s) for s in self._pending.values())

        log.debug("paths_queued", worktree=worktree, new_paths=len(paths), total_pending=count)

        # Schedule debounced flush
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        """Schedule a debounced flush of pending paths."""
        loop = asyncio.get_event_loop()

        # Cancel existing debounce task
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()

        self._debounce_task = loop.create_task(self._debounced_flush())

    async def _debounced_flush(self) -> None:
        """Wait for debounce period then flush."""
        try:
            await asyncio.sleep(self.config.debounce_sec)
            await self._flush()
        except asyncio.CancelledError:
            pass

    async def _flush(self) -> None:
        """Flush pending paths to the indexer."""
        if self._executor is None or self._state == IndexerState.STOPPING:
            return

        # Atomically grab and clear pending paths
        with self._pending_lock:
            if not self._pending:
                return
            snapshot = {wt: list(ps) for wt, ps in self._pending.items() if ps}
            self._pending.clear()

        if not snapshot:
            return

        # Each worktree's changed files must be indexed under its own worktree
        # tag so the lexical/vector indexes can discriminate by worktree.
        affected_worktrees = list(snapshot.keys())

        # Mark all affected worktrees stale BEFORE indexing
        for wt in affected_worktrees:
            self.gate.mark_stale(wt)

        self._state = IndexerState.INDEXING

        try:
            # Calculate total file count across all worktrees for the spinner.
            total_files = sum(len(paths) for paths in snapshot.values())
            spinner_msg = f"Reindexing {pluralize(total_files, 'file')}"

            # Use spinner with log suppression (Issue #5)
            with spinner(spinner_msg):
                # Index each worktree's files separately so documents are tagged
                # with the correct worktree in Tantivy/vector stores.
                loop = asyncio.get_event_loop()
                stats = await loop.run_in_executor(
                    self._executor,
                    self._index_sync,
                    snapshot,
                )

            all_paths = [p for ps in snapshot.values() for p in ps]
            self._last_stats = stats
            self._last_error = None

            # Build delta-based summary
            parts: list[str] = []
            if stats.files_added:
                parts.append(f"{stats.files_added} files created")
            if stats.files_updated:
                parts.append(f"{stats.files_updated} files updated")
            if stats.files_removed:
                parts.append(f"{stats.files_removed} files deleted")

            # Only print status when there were actual changes
            if parts:
                summary = ", ".join(parts)
                status(
                    f"{summary} ({stats.duration_seconds:.1f}s)", style="success", source="indexer"
                )

            # Notify completion callbacks
            for cb in self._on_complete_callbacks:
                await cb(stats, all_paths)

        except Exception as e:
            self._last_error = str(e)
            log.error("indexing_failed", error=str(e))

        finally:
            # Mark all affected worktrees fresh
            for wt in affected_worktrees:
                self.gate.mark_fresh(wt)
            self._state = IndexerState.IDLE

    def _index_sync(self, snapshot: dict[str, list[Path]]) -> IndexStats:
        """Synchronous indexing - runs in thread pool.

        Iterates over each worktree's changed files and calls
        ``reindex_incremental`` with the correct worktree tag so that
        documents end up in the right column partition of the shared index.
        """
        from coderecon.index.ops import IndexStats as _IndexStats

        combined = _IndexStats(
            files_processed=0,
            files_added=0,
            files_updated=0,
            files_removed=0,
            symbols_indexed=0,
            duration_seconds=0.0,
        )
        for wt, paths in snapshot.items():
            if not paths:
                continue
            partial = asyncio.run(
                self.coordinator.reindex_incremental(paths, worktree=wt)
            )
            combined.files_processed += partial.files_processed
            combined.files_added += partial.files_added
            combined.files_updated += partial.files_updated
            combined.files_removed += partial.files_removed
            combined.symbols_indexed += partial.symbols_indexed
            combined.duration_seconds += partial.duration_seconds
        return combined

    def add_on_complete(self, callback: Callable[[IndexStats, list[Path]], Awaitable[None]]) -> None:
        """Register a callback to invoke after successful indexing.

        Multiple callbacks are supported (one per worktree). The callback
        receives both the stats and the list of paths that were just reindexed.
        """
        self._on_complete_callbacks.append(callback)

    @property
    def status(self) -> IndexerStatus:
        """Get current indexer status."""
        with self._pending_lock:
            queue_size = sum(len(s) for s in self._pending.values())
        return IndexerStatus(
            state=self._state,
            queue_size=queue_size,
            last_stats=self._last_stats,
            last_error=self._last_error,
        )
