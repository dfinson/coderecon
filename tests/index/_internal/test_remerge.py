"""Tests for remerge logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.index._internal.remerge import (
    RemergeResult,
    drop_worktree_data,
)


class TestRemergeResult:
    def test_defaults(self) -> None:
        r = RemergeResult()
        assert r.adopted == 0
        assert r.reindexed == []
        assert r.pruned == 0

    def test_to_dict(self) -> None:
        r = RemergeResult()
        r.adopted = 5
        r.reindexed = ["a.py", "b.py"]
        r.pruned = 1
        r.elapsed_ms = 42.5

        d = r.to_dict()
        assert d["adopted"] == 5
        assert d["reindexed"] == 2
        assert d["pruned"] == 1
        assert d["elapsed_ms"] == 42.5


class TestDropWorktreeData:
    def test_drop_empty_worktree(self, tmp_path: Path) -> None:
        """Drop on a worktree with no files should return 0."""
        from coderecon.index._internal.db import Database

        db = Database(tmp_path / "test.db")
        db.create_all()

        result = drop_worktree_data(db.engine, worktree_id=99)
        assert result == 0
