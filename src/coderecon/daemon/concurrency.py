"""Concurrency primitives for multi-repo / multi-worktree daemon.

FreshnessGate — per-worktree staleness tracking for a shared index.
MutationRouter — per-worktree mutation serialization with reindex backpressure.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from coderecon.index.ops import IndexCoordinatorEngine

log = structlog.get_logger(__name__)


class FreshnessGate:
    """Per-worktree freshness tracking for a shared index.

    When worktree "main" mutates files, only searches on "main" block.
    Searches on "feature-x" proceed unimpeded.

    Thread safety: all methods must be called from the asyncio event loop.
    The gate is not thread-safe; callers in thread-pool workers must
    schedule via ``loop.call_soon_threadsafe``.
    """

    def __init__(self) -> None:
        self._stale: set[str] = set()
        self._events: dict[str, asyncio.Event] = {}

    def mark_stale(self, worktree: str) -> None:
        """Mark *worktree* as stale.  SYNCHRONOUS — safe to call before ``create_task``."""
        self._stale.add(worktree)
        self._get_event(worktree).clear()

    def mark_fresh(self, worktree: str) -> None:
        """Mark *worktree* as fresh (reindex complete)."""
        self._stale.discard(worktree)
        self._get_event(worktree).set()

    async def wait_fresh(self, worktree: str, *, timeout: float = 30.0) -> bool:
        """Block until *worktree*'s pending mutations are indexed.

        Returns True if fresh, False on timeout.
        """
        if worktree not in self._stale:
            return True
        try:
            await asyncio.wait_for(self._get_event(worktree).wait(), timeout)
            return True
        except asyncio.TimeoutError:
            log.warning("freshness_timeout", worktree=worktree, timeout=timeout)
            return False

    async def wait_all_fresh(self, *, timeout: float = 30.0) -> bool:
        """Block until *all* worktrees are fresh.  Used by full reindex."""
        if not self._stale:
            return True
        events = [self._get_event(wt) for wt in list(self._stale)]
        try:
            await asyncio.wait_for(
                asyncio.gather(*(e.wait() for e in events)),
                timeout,
            )
            return True
        except asyncio.TimeoutError:
            return False

    def mark_all_stale(self) -> None:
        """Mark every known worktree as stale (full reindex)."""
        for wt in list(self._events):
            self.mark_stale(wt)

    def mark_all_fresh(self) -> None:
        """Mark every known worktree as fresh."""
        for wt in list(self._stale):
            self.mark_fresh(wt)

    def is_stale(self, worktree: str) -> bool:
        return worktree in self._stale

    def _get_event(self, worktree: str) -> asyncio.Event:
        evt = self._events.get(worktree)
        if evt is None:
            evt = asyncio.Event()
            evt.set()  # default: fresh
            self._events[worktree] = evt
        return evt


class MutationRouter:
    """Serializes mutations per worktree.  Provides reindex backpressure.

    Two sessions on the same worktree cannot interleave mutations.
    Two sessions on different worktrees CAN mutate concurrently — they
    serialize at the coordinator's ``_reconcile_lock``, but the async
    layer stays responsive.

    Lock hierarchy (callers must acquire in this order):
        session._exclusive_lock  →  MutationRouter.mutation()
        →  _reindex_semaphore  →  coordinator._reconcile_lock
        →  coordinator._tantivy_write_lock
    """

    def __init__(
        self,
        coordinator: IndexCoordinatorEngine,
        gate: FreshnessGate,
        *,
        max_inflight_reindexes: int = 2,
    ) -> None:
        self._coordinator = coordinator
        self._gate = gate
        self._locks: dict[str, asyncio.Lock] = {}
        self._reindex_semaphore = asyncio.Semaphore(max_inflight_reindexes)

    def _get_lock(self, worktree: str) -> asyncio.Lock:
        lock = self._locks.get(worktree)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[worktree] = lock
        return lock

    @asynccontextmanager
    async def mutation(self, worktree: str) -> AsyncIterator[None]:
        """Acquire exclusive mutation access for *worktree*.

        Usage in tool handler::

            async with router.mutation(wt_name):
                result = mutation_ops.write_source(edits)
                if result.applied:
                    await router.on_mutation(wt_name, changed_paths)
        """
        async with self._get_lock(worktree):
            yield

    async def on_mutation(self, worktree: str, paths: list[Path]) -> None:
        """Trigger scoped reindex after files are written.

        SYNC staleness mark + ASYNC reindex bounded by semaphore.
        """
        self._gate.mark_stale(worktree)

        async def _reindex() -> None:
            async with self._reindex_semaphore:
                try:
                    await self._coordinator.reindex_incremental(paths)
                finally:
                    self._gate.mark_fresh(worktree)

        asyncio.create_task(_reindex())
