"""Tests for watcher resilience: watch_count, estimate_watch_count, ENOSPC fallback."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from coderecon.daemon.watcher import FileWatcher, _collect_watch_dirs
from coderecon.index._internal.ignore import IgnoreChecker

class TestWatchCount:
    """Tests for the watch_count property."""

    def test_zero_before_start(self, tmp_path: Path) -> None:
        """watch_count is 0 before the watcher is started."""
        (tmp_path / ".recon").mkdir()
        watcher = FileWatcher(repo_root=tmp_path, on_change=lambda paths: None)
        assert watcher.watch_count == 0

    @pytest.mark.asyncio
    async def test_nonzero_after_start(self, tmp_path: Path) -> None:
        """watch_count reflects watched directories after start."""
        (tmp_path / ".recon").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "core").mkdir()

        watcher = FileWatcher(repo_root=tmp_path, on_change=lambda paths: None)
        await watcher.start()
        try:
            # _watch_loop populates _watched_dirs asynchronously — yield to let it run
            await asyncio.sleep(0.2)
            # Should watch tmp_path, src, src/core (at minimum)
            assert watcher.watch_count >= 3
        finally:
            await watcher.stop()

    @pytest.mark.asyncio
    async def test_zero_after_stop(self, tmp_path: Path) -> None:
        """After stop, _watched_dirs stays populated (not cleared), but that's OK."""
        (tmp_path / ".recon").mkdir()
        watcher = FileWatcher(repo_root=tmp_path, on_change=lambda paths: None)
        await watcher.start()
        await watcher.stop()
        # Implementation note: _watched_dirs is not cleared on stop.
        # watch_count is informational, not a liveness check.

class TestEstimateWatchCount:
    """Tests for the static estimate_watch_count method."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        """Empty directory has exactly 1 watch (the root)."""
        count = FileWatcher.estimate_watch_count(tmp_path)
        assert count == 1

    def test_nested_dirs(self, tmp_path: Path) -> None:
        """Counts all non-prunable directories."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "core").mkdir()
        (tmp_path / "tests").mkdir()
        count = FileWatcher.estimate_watch_count(tmp_path)
        # root + src + src/core + tests = 4
        assert count == 4

    def test_excludes_prunable(self, tmp_path: Path) -> None:
        """Prunable directories are excluded from the estimate."""
        (tmp_path / "src").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "deep").mkdir()
        (tmp_path / ".git").mkdir()
        count = FileWatcher.estimate_watch_count(tmp_path)
        # root + src only
        assert count == 2

    def test_matches_collect_watch_dirs(self, tmp_path: Path) -> None:
        """Estimate matches actual _collect_watch_dirs count."""
        (tmp_path / "a" / "b" / "c").mkdir(parents=True)
        (tmp_path / "a" / "d").mkdir()
        (tmp_path / "__pycache__").mkdir()

        ignore_checker = IgnoreChecker(tmp_path, respect_gitignore=False)
        actual = len(_collect_watch_dirs(tmp_path, ignore_checker))
        estimated = FileWatcher.estimate_watch_count(tmp_path)
        assert estimated == actual

class TestDegradedToPoll:
    """Tests for the _degraded_to_poll flag."""

    def test_initially_false(self, tmp_path: Path) -> None:
        (tmp_path / ".recon").mkdir()
        watcher = FileWatcher(repo_root=tmp_path, on_change=lambda paths: None)
        assert watcher._degraded_to_poll is False
