"""Tests for git/_internal/access_index.py — GitIndex subprocess wrapper.

Covers:
- GitIndex add / remove / write / read
- GitIndex write_tree
- GitIndex conflicts property (ls-files -u parsing)
- GitIndex diff_to_tree
- GitIndex __contains__ / __getitem__ / __len__ / __iter__
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from coderecon.adapters.git._internal.access_index import GitIndex
from coderecon.adapters.git._internal.access_models import GitIndexEntry


def _make_index() -> tuple[GitIndex, MagicMock]:
    git = MagicMock()
    idx = GitIndex(git, Path("/repo"))
    return idx, git


class TestGitIndexAdd:
    def test_add_string_path(self) -> None:
        idx, git = _make_index()
        idx.add("src/main.py")
        git.run.assert_called_once_with("add", "--", "src/main.py")

    def test_add_entry_object(self) -> None:
        idx, git = _make_index()
        entry = GitIndexEntry("lib/util.py", "abc123", 0o100644)
        idx.add(entry)
        git.run.assert_called_once_with("add", "--", "lib/util.py")

    def test_add_invalidates_conflicts_cache(self) -> None:
        idx, git = _make_index()
        idx._conflicts = [("some", "conflict", "data")]
        idx.add("file.py")
        assert idx._conflicts is None


class TestGitIndexRemove:
    def test_remove(self) -> None:
        idx, git = _make_index()
        idx.remove("old.py")
        git.run.assert_called_once_with("rm", "--cached", "--", "old.py")

    def test_remove_invalidates_conflicts_cache(self) -> None:
        idx, git = _make_index()
        idx._conflicts = [("some", "conflict", "data")]
        idx.remove("file.py")
        assert idx._conflicts is None


class TestGitIndexWriteTree:
    def test_write_tree(self) -> None:
        idx, git = _make_index()
        git.run.return_value = SimpleNamespace(stdout="abc123def\n")
        sha = idx.write_tree()
        assert sha == "abc123def"
        git.run.assert_called_once_with("write-tree")


class TestGitIndexConflicts:
    def test_no_conflicts(self) -> None:
        idx, git = _make_index()
        git.run.return_value = SimpleNamespace(stdout="")
        assert idx.conflicts is None

    def test_with_conflicts(self) -> None:
        idx, git = _make_index()
        sha1 = "a" * 40
        sha2 = "b" * 40
        sha3 = "c" * 40
        git.run.return_value = SimpleNamespace(
            stdout=(
                f"100644 {sha1} 1\tsrc/conflict.py\n"
                f"100644 {sha2} 2\tsrc/conflict.py\n"
                f"100644 {sha3} 3\tsrc/conflict.py\n"
            )
        )
        conflicts = idx.conflicts
        assert conflicts is not None
        assert len(conflicts) == 1
        base, ours, theirs = conflicts[0]
        assert base.path == "src/conflict.py"
        assert base.sha == sha1
        assert ours.sha == sha2
        assert theirs.sha == sha3

    def test_conflicts_cached(self) -> None:
        idx, git = _make_index()
        git.run.return_value = SimpleNamespace(stdout="")
        idx.conflicts  # first call
        idx.conflicts  # second call should use cache
        assert git.run.call_count == 1


class TestGitIndexContains:
    def test_contains_existing(self) -> None:
        idx, git = _make_index()
        git.run_raw.return_value = (0, "src/main.py\n", "")
        assert "src/main.py" in idx

    def test_not_contains(self) -> None:
        idx, git = _make_index()
        git.run_raw.return_value = (1, "", "error")
        assert "missing.py" not in idx


class TestGitIndexGetitem:
    def test_getitem(self) -> None:
        idx, git = _make_index()
        sha = "d" * 40
        git.run.return_value = SimpleNamespace(stdout=f"100644 {sha} 0\tsrc/main.py\n")
        entry = idx["src/main.py"]
        assert isinstance(entry, GitIndexEntry)
        assert entry.path == "src/main.py"
        assert entry.sha == sha
        assert entry.mode == 0o100644

    def test_getitem_missing_raises_keyerror(self) -> None:
        idx, git = _make_index()
        git.run.return_value = SimpleNamespace(stdout="")
        with pytest.raises(KeyError, match="nonexistent.py"):
            idx["nonexistent.py"]


class TestGitIndexLen:
    def test_len_nonempty(self) -> None:
        idx, git = _make_index()
        git.run.return_value = SimpleNamespace(stdout="file1.py\nfile2.py\nfile3.py\n")
        assert len(idx) == 3

    def test_len_empty(self) -> None:
        idx, git = _make_index()
        git.run.return_value = SimpleNamespace(stdout="")
        assert len(idx) == 0


class TestGitIndexIter:
    def test_iter(self) -> None:
        idx, git = _make_index()
        sha = "e" * 40
        git.run.return_value = SimpleNamespace(
            stdout=f"100644 {sha} 0\ta.py\n100644 {sha} 0\tb.py\n"
        )
        entries = list(idx)
        assert len(entries) == 2
        assert entries[0].path == "a.py"
        assert entries[1].path == "b.py"

    def test_iter_empty(self) -> None:
        idx, git = _make_index()
        git.run.return_value = SimpleNamespace(stdout="")
        assert list(idx) == []


class TestGitIndexDiffToTree:
    def test_diff_to_tree(self) -> None:
        idx, git = _make_index()
        git.run.return_value = SimpleNamespace(stdout="diff --git a/x b/x\n...")
        result = idx.diff_to_tree("abc123")
        assert "diff" in result
        git.run.assert_called_once_with("diff-index", "-p", "--no-color", "abc123")
