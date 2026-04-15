"""Tests for content-hash deduplication."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.index._internal.dedup import find_reusable_files


class TestFindReusableFiles:
    def test_empty_input(self, tmp_path: Path) -> None:
        from coderecon.index._internal.db import Database

        db = Database(tmp_path / "test.db")
        db.create_all()

        result = find_reusable_files(db.engine, worktree_id=1, file_hashes={})
        assert result == {}

    def test_no_matches(self, tmp_path: Path) -> None:
        from coderecon.index._internal.db import Database

        db = Database(tmp_path / "test.db")
        db.create_all()

        result = find_reusable_files(
            db.engine,
            worktree_id=1,
            file_hashes={"src/foo.py": "hash123"},
        )
        assert result == {}

    def test_finds_match_from_other_worktree(self, tmp_path: Path) -> None:
        from sqlalchemy import text

        from coderecon.index._internal.db import Database

        db = Database(tmp_path / "test.db")
        db.create_all()

        # Insert a file in worktree 0 (main)
        with db.engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO files (id, worktree_id, path, content_hash) "
                "VALUES (1, 0, 'src/foo.py', 'abc123')"
            ))
            conn.commit()

        # Query from worktree 1 — should find the match
        result = find_reusable_files(
            db.engine,
            worktree_id=1,
            file_hashes={"src/foo.py": "abc123"},
        )
        assert "src/foo.py" in result
        assert result["src/foo.py"] == 1  # source file_id

    def test_ignores_same_worktree(self, tmp_path: Path) -> None:
        from sqlalchemy import text

        from coderecon.index._internal.db import Database

        db = Database(tmp_path / "test.db")
        db.create_all()

        # Insert a file in worktree 1
        with db.engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO files (id, worktree_id, path, content_hash) "
                "VALUES (1, 1, 'src/foo.py', 'abc123')"
            ))
            conn.commit()

        # Query from worktree 1 — should NOT find itself
        result = find_reusable_files(
            db.engine,
            worktree_id=1,
            file_hashes={"src/foo.py": "abc123"},
        )
        assert result == {}
