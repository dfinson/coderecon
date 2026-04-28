"""Tests for git/_internal/access_worktree.py — worktree and submodule mixin.

Covers:
- list_worktrees / worktree_path / worktree_is_prunable
- is_worktree / workdir / worktree_gitdir
- listall_submodules / lookup_submodule / submodule_name_for_path
- merge_base / diff_numstat
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock

import pytest

from coderecon.git._internal.access_worktree import _WorktreeMixin
from coderecon.git.errors import GitError


class _Stub(_WorktreeMixin):
    """Minimal stub wiring _git and path for mixin tests."""

    def __init__(self, git: MagicMock, path: Path, git_dir: Path | None = None) -> None:
        self._git = git
        self.path = path
        self._git_dir = git_dir or path / ".git"

    # Required by add_worktree
    def has_local_branch(self, name: str) -> bool:
        return False

    # Required by run_remote_operation
    def get_remote(self, name: str) -> None:
        pass


def _make(tmp_path: Path) -> tuple[_Stub, MagicMock]:
    git = MagicMock()
    stub = _Stub(git, tmp_path)
    return stub, git


# ===========================================================================
# list_worktrees
# ===========================================================================

class TestListWorktrees:
    def test_lists_non_main_worktrees(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run.return_value = SimpleNamespace(stdout=(
            "worktree /repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /repo/.worktrees/feature\n"
            "HEAD def456\n"
            "branch refs/heads/feature\n"
        ))
        names = s.list_worktrees()
        assert names == ["feature"]

    def test_empty_when_only_main(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run.return_value = SimpleNamespace(stdout=(
            "worktree /repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
        ))
        assert s.list_worktrees() == []

    def test_multiple_worktrees(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run.return_value = SimpleNamespace(stdout=(
            "worktree /repo\n\n"
            "worktree /repo/.wt/alpha\n\n"
            "worktree /repo/.wt/beta\n\n"
        ))
        assert s.list_worktrees() == ["alpha", "beta"]


# ===========================================================================
# worktree_path
# ===========================================================================

class TestWorktreePath:
    def test_returns_path_by_name(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run.return_value = SimpleNamespace(stdout=(
            "worktree /main\n\n"
            "worktree /wt/feature-x\n\n"
        ))
        assert s.worktree_path("feature-x") == "/wt/feature-x"

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run.return_value = SimpleNamespace(stdout="worktree /main\n")
        with pytest.raises(GitError, match="not found"):
            s.worktree_path("missing")


# ===========================================================================
# is_worktree
# ===========================================================================

class TestIsWorktree:
    def test_is_worktree_when_dotgit_is_file(self, tmp_path: Path) -> None:
        (tmp_path / ".git").write_text("gitdir: /somewhere/.git/worktrees/feat")
        s, _ = _make(tmp_path)
        assert s.is_worktree() is True

    def test_not_worktree_when_dotgit_is_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        s, _ = _make(tmp_path)
        assert s.is_worktree() is False


# ===========================================================================
# workdir
# ===========================================================================

class TestWorkdir:
    def test_returns_toplevel_with_slash(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run_raw.return_value = (0, "/repo\n", "")
        assert s.workdir == "/repo/"

    def test_none_on_failure(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run_raw.return_value = (128, "", "fatal")
        assert s.workdir is None


# ===========================================================================
# worktree_gitdir
# ===========================================================================

class TestWorktreeGitdir:
    def test_returns_correct_path(self, tmp_path: Path) -> None:
        s, _ = _make(tmp_path)
        gd = s.worktree_gitdir("feature")
        assert gd == tmp_path / ".git" / "worktrees" / "feature"


# ===========================================================================
# listall_submodules
# ===========================================================================

class TestListallSubmodules:
    def test_lists_submodules(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run_raw.return_value = (
            0,
            "submodule.vendor/lib.path vendor/lib\n"
            "submodule.tools/lint.path tools/lint\n",
            "",
        )
        assert s.listall_submodules() == ["vendor/lib", "tools/lint"]

    def test_empty_when_no_gitmodules(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run_raw.return_value = (1, "", "fatal: unable to read")
        assert s.listall_submodules() == []


# ===========================================================================
# lookup_submodule
# ===========================================================================

class TestLookupSubmodule:
    def test_returns_submodule_info(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        sha = "a" * 40
        git.run_raw.side_effect = [
            (0, "vendor/lib\n", ""),   # path
            (0, "https://github.com/org/lib.git\n", ""),  # url
            (0, "main\n", ""),         # branch
            (0, f"160000 commit {sha}\tvendor/lib\n", ""),  # ls-tree
        ]
        sm = s.lookup_submodule("vendor/lib")
        assert sm["name"] == "vendor/lib"
        assert sm["path"] == "vendor/lib"
        assert sm["url"] == "https://github.com/org/lib.git"
        assert sm["branch"] == "main"
        assert sm["head_id"] == sha

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run_raw.return_value = (1, "", "not found")
        with pytest.raises(GitError, match="not found"):
            s.lookup_submodule("missing")


# ===========================================================================
# merge_base
# ===========================================================================

class TestMergeBase:
    def test_returns_merge_base(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        sha = "b" * 40
        git.run_raw.return_value = (0, sha + "\n", "")
        assert s.merge_base("sha1", "sha2") == sha

    def test_none_when_unrelated(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run_raw.return_value = (1, "", "fatal")
        assert s.merge_base("sha1", "sha2") is None


# ===========================================================================
# diff_numstat
# ===========================================================================

class TestDiffNumstat:
    def test_parses_numstat_output(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run.side_effect = [
            SimpleNamespace(stdout="10\t5\tsrc/main.py\n3\t0\tREADME.md\n"),  # numstat
            SimpleNamespace(stdout="M\tsrc/main.py\nA\tREADME.md\n"),  # name-status
        ]
        entries = s.diff_numstat("HEAD~1")
        assert len(entries) == 2
        assert entries[0] == ("M", 10, 5, "src/main.py")
        assert entries[1] == ("A", 3, 0, "README.md")

    def test_handles_binary_files(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run.side_effect = [
            SimpleNamespace(stdout="-\t-\timage.png\n"),
            SimpleNamespace(stdout="M\timage.png\n"),
        ]
        entries = s.diff_numstat("HEAD~1")
        assert len(entries) == 1
        assert entries[0] == ("M", 0, 0, "image.png")

    def test_empty_diff(self, tmp_path: Path) -> None:
        s, git = _make(tmp_path)
        git.run.side_effect = [
            SimpleNamespace(stdout=""),
            SimpleNamespace(stdout=""),
        ]
        assert s.diff_numstat("HEAD") == []
