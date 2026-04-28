"""Tests for index/ops_reindex_full.py helper functions and pure logic."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


from coderecon.index.ops_reindex_full import (
    _detect_changed_files,
    _structural_index_added_files,
    _upsert_file_records,
)


class TestDetectChangedFiles:
    """Tests for _detect_changed_files — identifies on-disk hash mismatches."""

    def _make_engine(self, tmp_path: Path, file_contents: dict[str, bytes]) -> MagicMock:
        engine = MagicMock()
        engine.repo_root = tmp_path
        for rel, content in file_contents.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        return engine

    def test_no_common_paths_returns_empty(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path, {})
        result = _detect_changed_files(engine, set(), {})
        assert result == set()

    def test_unchanged_file_not_detected(self, tmp_path: Path) -> None:
        content = b"hello world"
        engine = self._make_engine(tmp_path, {"a.py": content})
        disk_hash = hashlib.sha256(content).hexdigest()
        result = _detect_changed_files(engine, {"a.py"}, {"a.py": disk_hash})
        assert result == set()

    def test_changed_file_detected(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path, {"a.py": b"new content"})
        result = _detect_changed_files(engine, {"a.py"}, {"a.py": "oldhash"})
        assert result == {"a.py"}

    def test_missing_file_skipped(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path, {})
        result = _detect_changed_files(engine, {"gone.py"}, {"gone.py": "hash"})
        assert result == set()

    def test_os_error_raises(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path, {"a.py": b"content"})
        with patch("coderecon.index.ops_reindex_full.hashlib") as mock_hash:
            mock_hash.sha256.side_effect = OSError("read error")
            with pytest.raises(OSError, match="read error"):
                _detect_changed_files(engine, {"a.py"}, {"a.py": "hash"})

    def test_multiple_files_mixed(self, tmp_path: Path) -> None:
        content_a = b"unchanged"
        content_b = b"modified"
        engine = self._make_engine(tmp_path, {"a.py": content_a, "b.py": content_b})
        hashes = {
            "a.py": hashlib.sha256(content_a).hexdigest(),
            "b.py": "stale_hash",
        }
        result = _detect_changed_files(engine, {"a.py", "b.py"}, hashes)
        assert result == {"b.py"}

    def test_none_hash_in_db_triggers_update(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path, {"a.py": b"content"})
        result = _detect_changed_files(engine, {"a.py"}, {"a.py": None})
        assert result == {"a.py"}


class TestUpsertFileRecords:
    """Tests for _upsert_file_records — creates/updates File rows."""

    def test_empty_sets_returns_empty(self) -> None:
        engine = MagicMock()
        result = _upsert_file_records(engine, set(), set(), worktree_id=1)
        assert result == {}
        engine.db.session.assert_not_called()

    def test_add_creates_file_record(self, tmp_path: Path) -> None:
        content = b"some content"
        (tmp_path / "new.py").write_bytes(content)
        engine = MagicMock()
        engine.repo_root = tmp_path

        session = MagicMock()
        engine.db.session.return_value.__enter__ = MagicMock(return_value=session)
        engine.db.session.return_value.__exit__ = MagicMock(return_value=False)

        # Make session.add capture the File record and simulate flush setting id
        added_records: list[object] = []

        def capture_add(record: object) -> None:
            added_records.append(record)
            record.id = 42  # type: ignore[attr-defined]

        session.add.side_effect = capture_add

        result = _upsert_file_records(engine, {"new.py"}, set(), worktree_id=1)
        assert result == {"new.py": 42}
        session.commit.assert_called_once()

    def test_update_sets_content_hash(self, tmp_path: Path) -> None:
        content = b"updated content"
        (tmp_path / "existing.py").write_bytes(content)
        engine = MagicMock()
        engine.repo_root = tmp_path

        session = MagicMock()
        engine.db.session.return_value.__enter__ = MagicMock(return_value=session)
        engine.db.session.return_value.__exit__ = MagicMock(return_value=False)

        existing_file = MagicMock()
        existing_file.id = 7
        session.exec.return_value.first.return_value = existing_file

        result = _upsert_file_records(engine, set(), {"existing.py"}, worktree_id=1)
        assert result == {"existing.py": 7}
        assert existing_file.content_hash == hashlib.sha256(content).hexdigest()
        session.commit.assert_called_once()

    def test_missing_file_skipped_for_add(self, tmp_path: Path) -> None:
        engine = MagicMock()
        engine.repo_root = tmp_path

        session = MagicMock()
        engine.db.session.return_value.__enter__ = MagicMock(return_value=session)
        engine.db.session.return_value.__exit__ = MagicMock(return_value=False)

        result = _upsert_file_records(engine, {"missing.py"}, set(), worktree_id=1)
        assert result == {}
        session.add.assert_not_called()


class TestStructuralIndexAddedFiles:
    """Tests for _structural_index_added_files — groups by context and indexes."""

    @patch("coderecon.index._internal.indexing.config_refs.resolve_config_file_refs")
    @patch("coderecon.index.ops_reindex_full.run_pass_1_5")
    @patch("coderecon.index.ops_reindex_full.resolve_references")
    def test_groups_by_context_and_calls_structural(
        self,
        mock_resolve: MagicMock,
        mock_pass_1_5: MagicMock,
        mock_config_refs: MagicMock,
    ) -> None:
        engine = MagicMock()
        engine._freshness_worktree = "main"
        engine.repo_root = Path("/repo")
        engine._worktree_root_cache = {"main": Path("/repo")}
        engine._get_or_create_worktree_id.return_value = 1
        engine._is_main_worktree.return_value = True

        to_add = {"a.py", "b.py", "c.py"}
        file_to_context = {"a.py": 1, "b.py": 1, "c.py": 2}
        file_id_map = {"a.py": 10, "b.py": 11, "c.py": 12}

        _structural_index_added_files(engine, to_add, file_to_context, file_id_map)

        # Should call extract_files and index_files for each context group
        assert engine._structural.extract_files.call_count == 2
        assert engine._structural.index_files.call_count == 2
        mock_config_refs.assert_called_once()
        mock_pass_1_5.assert_called_once()
        mock_resolve.assert_called_once()

    @patch("coderecon.index._internal.indexing.config_refs.resolve_config_file_refs")
    @patch("coderecon.index.ops_reindex_full.run_pass_1_5")
    @patch("coderecon.index.ops_reindex_full.resolve_references")
    def test_default_context_id_when_missing(
        self,
        mock_resolve: MagicMock,
        mock_pass_1_5: MagicMock,
        mock_config_refs: MagicMock,
    ) -> None:
        engine = MagicMock()
        engine._freshness_worktree = "main"
        engine.repo_root = Path("/repo")
        engine._worktree_root_cache = {"main": Path("/repo")}

        to_add = {"x.py"}
        file_to_context: dict[str, int] = {}  # no explicit context
        file_id_map = {"x.py": 1}

        _structural_index_added_files(engine, to_add, file_to_context, file_id_map)

        # File with no context should fall back to context_id 1
        call_args = engine._structural.extract_files.call_args
        assert call_args[0][1] == 1  # context_id argument
