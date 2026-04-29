"""Tests for index/ops_reindex.py module.

Covers:
- _normalize_paths() absolute → relative conversion
- _assign_contexts() context routing for changed files
- _create_new_file_records() file record creation with mocked engine
- _classify_paths() path classification with mocked DB
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from coderecon.index.ops_reindex import (
    _assign_contexts,
    _classify_paths,
    _create_new_file_records,
    _normalize_paths,
)


class TestNormalizePaths:
    """_normalize_paths converts absolute paths to repo-relative."""

    def test_already_relative(self) -> None:
        result = _normalize_paths(
            [Path("src/foo.py")],
            Path("/repo"),
            Path("/repo"),
        )
        assert result == [Path("src/foo.py")]

    def test_absolute_under_effective_root(self) -> None:
        result = _normalize_paths(
            [Path("/worktree/src/foo.py")],
            Path("/worktree"),
            Path("/repo"),
        )
        assert result == [Path("src/foo.py")]

    def test_absolute_under_repo_root_fallback(self) -> None:
        result = _normalize_paths(
            [Path("/repo/src/foo.py")],
            Path("/worktree"),
            Path("/repo"),
        )
        assert result == [Path("src/foo.py")]

    def test_unresolvable_path_skipped(self) -> None:
        result = _normalize_paths(
            [Path("/other/place/foo.py")],
            Path("/worktree"),
            Path("/repo"),
        )
        assert result == []

    def test_mixed_paths(self) -> None:
        result = _normalize_paths(
            [
                Path("relative.py"),
                Path("/worktree/abs.py"),
                Path("/nowhere/gone.py"),
            ],
            Path("/worktree"),
            Path("/repo"),
        )
        assert len(result) == 2
        assert Path("relative.py") in result
        assert Path("abs.py") in result

    def test_deduplication_preserves_order(self) -> None:
        """normalize_paths doesn't dedup — that's the caller's job."""
        result = _normalize_paths(
            [Path("a.py"), Path("a.py")],
            Path("/repo"),
            Path("/repo"),
        )
        assert len(result) == 2


class TestAssignContexts:
    """_assign_contexts maps file paths to owning context_id."""

    def _make_ctx(
        self,
        ctx_id: int,
        root_path: str,
        tier: int = 1,
        exclude: list[str] | None = None,
        include: list[str] | None = None,
    ) -> MagicMock:
        ctx = MagicMock()
        ctx.id = ctx_id
        ctx.root_path = root_path
        ctx.tier = tier
        ctx.get_exclude_globs.return_value = exclude or []
        ctx.get_include_globs.return_value = include or []
        return ctx

    def test_file_under_context_root(self) -> None:
        ctx = self._make_ctx(1, "src")
        result = _assign_contexts([ctx], ["src/foo.py"])
        assert result == {"src/foo.py": 1}

    def test_file_outside_context_not_assigned(self) -> None:
        ctx = self._make_ctx(1, "src")
        result = _assign_contexts([ctx], ["tests/test_foo.py"])
        assert result == {}

    def test_root_fallback_catches_unclaimed(self) -> None:
        specific = self._make_ctx(1, "src")
        fallback = self._make_ctx(99, "", tier=3)
        result = _assign_contexts(
            [specific, fallback],
            ["README.md"],
        )
        assert result == {"README.md": 99}

    def test_most_specific_context_wins(self) -> None:
        """Longer root_path is preferred (contexts sorted by root_path length desc)."""
        broad = self._make_ctx(1, "src")
        narrow = self._make_ctx(2, "src/pkg")
        result = _assign_contexts([broad, narrow], ["src/pkg/mod.py"])
        assert result["src/pkg/mod.py"] == 2

    def test_exclude_globs_respected(self) -> None:
        ctx = self._make_ctx(1, "src", exclude=["*.generated.py"])
        result = _assign_contexts([ctx], ["src/foo.generated.py"])
        assert result == {}

    def test_include_globs_filter(self) -> None:
        ctx = self._make_ctx(1, "src", include=["**/*.py"])
        result = _assign_contexts([ctx], ["src/data.json"])
        assert result == {}

    def test_empty_input(self) -> None:
        ctx = self._make_ctx(1, "src")
        result = _assign_contexts([ctx], [])
        assert result == {}

    def test_context_with_no_id_skipped(self) -> None:
        ctx = self._make_ctx(None, "src")  # type: ignore[arg-type]
        result = _assign_contexts([ctx], ["src/foo.py"])
        assert result == {}

    def test_exact_root_path_match(self) -> None:
        """A file whose path equals the context root_path is assigned."""
        ctx = self._make_ctx(1, "Makefile")
        result = _assign_contexts([ctx], ["Makefile"])
        # The code checks str_path == ctx_root OR str_path.startswith(ctx_root + "/")
        assert result.get("Makefile") == 1


class TestClassifyPaths:
    """_classify_paths splits paths into existing, new, removed."""

    def _make_db(self, indexed_paths: list[str]) -> MagicMock:
        db = MagicMock()
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.exec.return_value.all.return_value = indexed_paths
        db.session.return_value = session
        return db

    def test_existing_file_classified(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1")
        db = self._make_db(["a.py"])
        existing, new, removed = _classify_paths(
            [Path("a.py")], 1, tmp_path, db,
        )
        assert existing == [Path("a.py")]
        assert new == []
        assert removed == []

    def test_new_file_classified(self, tmp_path: Path) -> None:
        (tmp_path / "b.py").write_text("y = 2")
        db = self._make_db([])
        existing, new, removed = _classify_paths(
            [Path("b.py")], 1, tmp_path, db,
        )
        assert existing == []
        assert new == [Path("b.py")]
        assert removed == []

    def test_removed_file_classified(self, tmp_path: Path) -> None:
        # File doesn't exist on disk but is in the DB
        db = self._make_db(["gone.py"])
        existing, new, removed = _classify_paths(
            [Path("gone.py")], 1, tmp_path, db,
        )
        assert existing == []
        assert new == []
        assert removed == [Path("gone.py")]

    def test_missing_and_not_indexed_ignored(self, tmp_path: Path) -> None:
        """File neither on disk nor in DB → not in any category."""
        db = self._make_db([])
        existing, new, removed = _classify_paths(
            [Path("phantom.py")], 1, tmp_path, db,
        )
        assert existing == []
        assert new == []
        assert removed == []

    def test_mixed_classification(self, tmp_path: Path) -> None:
        (tmp_path / "kept.py").write_text("")
        (tmp_path / "fresh.py").write_text("")
        db = self._make_db(["kept.py", "deleted.py"])
        existing, new, removed = _classify_paths(
            [Path("kept.py"), Path("fresh.py"), Path("deleted.py")],
            1,
            tmp_path,
            db,
        )
        assert existing == [Path("kept.py")]
        assert new == [Path("fresh.py")]
        assert removed == [Path("deleted.py")]


class TestCreateNewFileRecords:
    """_create_new_file_records creates File rows for new paths."""

    def test_empty_paths_returns_zero(self) -> None:
        engine = MagicMock()
        file_id_map: dict[str, int] = {}
        result = _create_new_file_records([], Path("/repo"), 1, engine, file_id_map)
        assert result == 0
        assert file_id_map == {}

    def test_creates_file_record(self, tmp_path: Path) -> None:
        (tmp_path / "new.py").write_text("print('hello')")

        engine = MagicMock()
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        engine.db.session.return_value = session

        file_record = MagicMock()
        file_record.id = 42

        def fake_add(obj: object) -> None:
            # Simulate SQLModel setting id on flush
            obj.id = 42  # type: ignore[attr-defined]

        session.add.side_effect = fake_add

        file_id_map: dict[str, int] = {}
        with patch("coderecon.index.ops_reindex.detect_language_family", return_value="python"):
            result = _create_new_file_records(
                [Path("new.py")], tmp_path, 1, engine, file_id_map,
            )

        assert result == 1
        assert file_id_map["new.py"] == 42

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        engine = MagicMock()
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        engine.db.session.return_value = session

        file_id_map: dict[str, int] = {}
        result = _create_new_file_records(
            [Path("missing.py")], tmp_path, 1, engine, file_id_map,
        )
        assert result == 0
