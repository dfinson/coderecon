"""Tests for idle eviction loop and watch ceiling in GlobalDaemon."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from coderecon.adapters.catalog.registry import CatalogRegistry
from coderecon.daemon.global_app import (
    GlobalDaemon,
    RepoSlot,
    WorktreeSlot,
    _DEFAULT_WATCH_CEILING,
)

@pytest.fixture
def daemon(registry: CatalogRegistry) -> GlobalDaemon:
    return GlobalDaemon(registry)

def _stub_watcher(watch_count: int = 100) -> Any:
    """Create a minimal stub that quacks like a FileWatcher."""
    watcher = AsyncMock()
    watcher.watch_count = watch_count
    watcher.stop = AsyncMock()
    return watcher

def _make_wt_slot(
    name: str = "wt-1",
    *,
    last_request_at: float | None = None,
    watch_count: int = 100,
) -> WorktreeSlot:
    """Create a WorktreeSlot with stubs for testing."""
    watcher = _stub_watcher(watch_count)
    slot = WorktreeSlot(
        name=name,
        repo_root=Path("/fake"),
        watcher=watcher,
        app_ctx=AsyncMock(),
        session_manager=AsyncMock(),
        mcp=AsyncMock(),
        mcp_asgi_app=AsyncMock(),
        _mcp_lifespan_ctx=AsyncMock(),
    )
    if last_request_at is not None:
        slot.last_request_at = last_request_at
    return slot

class TestEvictionLoop:
    """Tests for the idle eviction background task."""

    @pytest.mark.asyncio
    async def test_evicts_idle_worktree(self, daemon: GlobalDaemon) -> None:
        """A non-main worktree idle beyond the timeout gets torn down."""
        # Set up a repo slot with main + one idle worktree
        slot = RepoSlot(
            name="repo-a",
            repo_id=1,
            storage_dir=Path("/fake/storage"),
            coordinator=AsyncMock(),
            gate=AsyncMock(),
            router=AsyncMock(),
            indexer=AsyncMock(),
        )
        slot.worktrees["main"] = _make_wt_slot("main")
        slot.worktrees["wt-1"] = _make_wt_slot(
            "wt-1", last_request_at=time.time() - 600,
        )
        daemon._slots["repo-a"] = slot

        # Start eviction with a very short timeout so it fires immediately
        daemon.start_eviction_loop(idle_timeout=0.01)
        await asyncio.sleep(0.1)

        # wt-1 should have been evicted
        assert "wt-1" not in slot.worktrees
        # main must survive
        assert "main" in slot.worktrees

        # Cleanup
        daemon._eviction_task.cancel()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_does_not_evict_active_worktree(self, daemon: GlobalDaemon) -> None:
        """A recently-active non-main worktree is NOT evicted."""
        slot = RepoSlot(
            name="repo-a",
            repo_id=1,
            storage_dir=Path("/fake/storage"),
            coordinator=AsyncMock(),
            gate=AsyncMock(),
            router=AsyncMock(),
            indexer=AsyncMock(),
        )
        slot.worktrees["main"] = _make_wt_slot("main")
        slot.worktrees["wt-1"] = _make_wt_slot(
            "wt-1", last_request_at=time.time(),
        )
        daemon._slots["repo-a"] = slot

        daemon.start_eviction_loop(idle_timeout=600.0)
        await asyncio.sleep(0.1)

        # wt-1 should still be present
        assert "wt-1" in slot.worktrees

        daemon._eviction_task.cancel()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_never_evicts_main(self, daemon: GlobalDaemon) -> None:
        """Main worktree is never evicted regardless of idle time."""
        slot = RepoSlot(
            name="repo-a",
            repo_id=1,
            storage_dir=Path("/fake/storage"),
            coordinator=AsyncMock(),
            gate=AsyncMock(),
            router=AsyncMock(),
            indexer=AsyncMock(),
        )
        slot.worktrees["main"] = _make_wt_slot(
            "main", last_request_at=time.time() - 9999,
        )
        daemon._slots["repo-a"] = slot

        daemon.start_eviction_loop(idle_timeout=0.01)
        await asyncio.sleep(0.1)

        assert "main" in slot.worktrees

        daemon._eviction_task.cancel()
        await asyncio.sleep(0)

    def test_zero_timeout_disables_eviction(self, daemon: GlobalDaemon) -> None:
        """Passing idle_timeout=0 does not start the eviction task."""
        daemon.start_eviction_loop(idle_timeout=0)
        assert daemon._eviction_task is None

    @pytest.mark.asyncio
    async def test_stop_all_cancels_eviction(self, daemon: GlobalDaemon) -> None:
        """stop_all() cancels the eviction loop."""
        daemon.start_eviction_loop(idle_timeout=60.0)
        assert daemon._eviction_task is not None
        await daemon.stop_all()
        assert daemon._eviction_task is None

class TestWatchCeiling:
    """Tests for inotify watch ceiling enforcement."""

    def test_default_ceiling(self, registry: CatalogRegistry) -> None:
        d = GlobalDaemon(registry)
        assert d._watch_ceiling == _DEFAULT_WATCH_CEILING

    def test_custom_ceiling(self, registry: CatalogRegistry) -> None:
        d = GlobalDaemon(registry, watch_ceiling=50)
        assert d._watch_ceiling == 50

    def test_current_watch_count_empty(self, daemon: GlobalDaemon) -> None:
        assert daemon._current_watch_count() == 0

    def test_current_watch_count_sums_worktrees(self, daemon: GlobalDaemon) -> None:
        slot = RepoSlot(
            name="repo-a",
            repo_id=1,
            storage_dir=Path("/fake/storage"),
            coordinator=AsyncMock(),
            gate=AsyncMock(),
            router=AsyncMock(),
            indexer=AsyncMock(),
        )
        slot.worktrees["main"] = _make_wt_slot("main", watch_count=200)
        slot.worktrees["wt-1"] = _make_wt_slot("wt-1", watch_count=150)
        daemon._slots["repo-a"] = slot

        assert daemon._current_watch_count() == 350

    @pytest.mark.asyncio
    async def test_lazy_activate_refuses_over_ceiling(
        self, daemon: GlobalDaemon, tmp_path: Path,
    ) -> None:
        """lazy_activate_worktree returns None when ceiling would be exceeded."""
        # Create a real repo slot with a main worktree that uses 150 watches
        slot = RepoSlot(
            name="repo-a",
            repo_id=1,
            storage_dir=Path("/fake/storage"),
            coordinator=AsyncMock(),
            gate=AsyncMock(),
            router=AsyncMock(),
            indexer=AsyncMock(),
        )
        slot.worktrees["main"] = _make_wt_slot("main", watch_count=150)
        daemon._slots["repo-a"] = slot
        daemon._watch_ceiling = 200  # Only 50 watches left

        # Create a real directory tree that will estimate > 50 watches
        wt_path = tmp_path / "big-worktree"
        for i in range(60):
            (wt_path / f"dir_{i}").mkdir(parents=True)

        # Mock the catalog lookup to return this path
        mock_entry = AsyncMock()
        mock_entry.root_path = str(wt_path)
        with patch.object(daemon.registry, "lookup_worktree", return_value=mock_entry):
            result = await daemon.lazy_activate_worktree("repo-a", "wt-big")

        assert result is None

class TestWorktreeSlotTimestamps:
    """Tests for activated_at and last_request_at fields."""

    def test_defaults_to_now(self) -> None:
        before = time.time()
        slot = _make_wt_slot("test")
        after = time.time()
        assert before <= slot.activated_at <= after
        assert before <= slot.last_request_at <= after

    def test_last_request_at_updates(self) -> None:
        slot = _make_wt_slot("test", last_request_at=100.0)
        assert slot.last_request_at == 100.0
        slot.last_request_at = time.time()
        assert slot.last_request_at > 100.0
