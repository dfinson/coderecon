"""Tests for git worktree operations mixin."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from coderecon.git.errors import (
    BranchNotFoundError,
    WorktreeError,
    WorktreeExistsError,
    WorktreeLockedError,
    WorktreeNotFoundError,
)
from coderecon.git.models import WorktreeInfo
from coderecon.git.ops_worktree import _WorktreeMixin


def _make_mixin(
    *,
    git_output: str = "",
    worktree_names: list[str] | None = None,
    lock_exists: bool = False,
    lock_content: str = "",
    is_worktree: bool = False,
    has_local_branch: bool = True,
) -> _WorktreeMixin:
    """Build a _WorktreeMixin instance with a mocked _access."""
    mixin = _WorktreeMixin.__new__(_WorktreeMixin)
    access = MagicMock()

    run_result = MagicMock()
    run_result.stdout = git_output
    access.git.run.return_value = run_result

    access.list_worktrees.return_value = worktree_names or []
    access.has_local_branch.return_value = has_local_branch
    access.is_worktree.return_value = is_worktree

    # worktree_gitdir returns a path whose / "locked" we can control
    gitdir = MagicMock(spec=Path)
    lock_file = MagicMock(spec=Path)
    lock_file.exists.return_value = lock_exists
    lock_file.read_text.return_value = lock_content
    gitdir.__truediv__ = MagicMock(return_value=lock_file)
    gitdir.exists.return_value = True
    access.worktree_gitdir.return_value = gitdir

    access.worktree_is_prunable.return_value = False

    mixin._access = access  # type: ignore[attr-defined]
    return mixin


# ---------------------------------------------------------------------------
# Porcelain output samples
# ---------------------------------------------------------------------------
PORCELAIN_TWO_WORKTREES = (
    "worktree /repo\n"
    "HEAD abc1234\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree /repo/.worktrees/feature\n"
    "HEAD def5678\n"
    "branch refs/heads/feature\n"
    "\n"
)

PORCELAIN_DETACHED = (
    "worktree /repo\n"
    "HEAD abc1234\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree /repo/.worktrees/detached-wt\n"
    "HEAD 0000000\n"
    "detached\n"
    "\n"
)

PORCELAIN_BARE = (
    "worktree /repo\n"
    "HEAD abc1234\n"
    "bare\n"
    "\n"
)

PORCELAIN_EMPTY = ""


class TestWorktreesList:
    """Tests for _WorktreeMixin.worktrees()."""

    def test_parses_two_worktrees(self) -> None:
        mixin = _make_mixin(git_output=PORCELAIN_TWO_WORKTREES)
        result = mixin.worktrees()
        assert len(result) == 2
        main, feature = result
        assert main.is_main is True
        assert main.name == "main"
        assert main.head_ref == "main"
        assert main.head_sha == "abc1234"
        assert feature.is_main is False
        assert feature.name == "feature"
        assert feature.head_ref == "feature"
        assert feature.head_sha == "def5678"

    def test_parses_detached_worktree(self) -> None:
        mixin = _make_mixin(git_output=PORCELAIN_DETACHED)
        result = mixin.worktrees()
        detached = result[1]
        assert detached.head_ref == "HEAD"

    def test_parses_bare_repo(self) -> None:
        mixin = _make_mixin(git_output=PORCELAIN_BARE)
        result = mixin.worktrees()
        assert len(result) == 1
        assert result[0].is_bare is True

    def test_empty_output(self) -> None:
        mixin = _make_mixin(git_output=PORCELAIN_EMPTY)
        result = mixin.worktrees()
        assert result == []

    def test_locked_worktree_detected(self) -> None:
        mixin = _make_mixin(
            git_output=PORCELAIN_TWO_WORKTREES,
            lock_exists=True,
            lock_content="CI lock",
        )
        result = mixin.worktrees()
        feature = result[1]
        assert feature.is_locked is True
        assert feature.lock_reason == "CI lock"

    def test_prunable_when_path_missing(self, tmp_path: Path) -> None:
        # Use a path that does not exist to trigger is_prunable
        porcelain = (
            "worktree /repo\n"
            "HEAD abc1234\n"
            "branch refs/heads/main\n"
            "\n"
            f"worktree {tmp_path / 'nonexistent'}\n"
            "HEAD def5678\n"
            "branch refs/heads/gone\n"
            "\n"
        )
        mixin = _make_mixin(git_output=porcelain)
        result = mixin.worktrees()
        assert result[1].is_prunable is True


class TestWorktreeAdd:
    """Tests for _WorktreeMixin.worktree_add()."""

    def test_raises_branch_not_found(self, tmp_path: Path) -> None:
        mixin = _make_mixin(has_local_branch=False)
        with pytest.raises(BranchNotFoundError):
            mixin.worktree_add(tmp_path / "wt", "no-such-branch")

    def test_raises_when_path_exists(self, tmp_path: Path) -> None:
        existing = tmp_path / "existing"
        existing.mkdir()
        mixin = _make_mixin()
        with pytest.raises(WorktreeError, match="Path already exists"):
            mixin.worktree_add(existing, "main")

    def test_raises_when_name_already_used(self, tmp_path: Path) -> None:
        mixin = _make_mixin(worktree_names=["feature"])
        with pytest.raises(WorktreeExistsError):
            mixin.worktree_add(tmp_path / "feature", "main")

    @patch("coderecon.git.ops_worktree.GitOps" if False else "coderecon.git.ops.GitOps")
    def test_success_returns_git_ops(self, mock_git_ops_cls: MagicMock, tmp_path: Path) -> None:
        mixin = _make_mixin(worktree_names=[])
        new_path = tmp_path / "new-wt"
        # The path must not exist yet
        mixin._access.worktree_path.side_effect = Exception  # type: ignore[attr-defined]
        mock_git_ops_cls.return_value = MagicMock()
        result = mixin.worktree_add(new_path, "main")
        mixin._access.add_worktree.assert_called_once_with(  # type: ignore[attr-defined]
            "new-wt", str(new_path), "main",
        )
        assert result is mock_git_ops_cls.return_value


class TestWorktreeRemove:
    """Tests for _WorktreeMixin.worktree_remove()."""

    def test_raises_not_found(self) -> None:
        mixin = _make_mixin(worktree_names=[])
        with pytest.raises(WorktreeNotFoundError):
            mixin.worktree_remove("ghost")

    def test_raises_locked(self) -> None:
        mixin = _make_mixin(worktree_names=["locked-wt"], lock_exists=True)
        with pytest.raises(WorktreeLockedError):
            mixin.worktree_remove("locked-wt")

    def test_force_removes_locked(self) -> None:
        mixin = _make_mixin(worktree_names=["locked-wt"], lock_exists=True)
        mixin.worktree_remove("locked-wt", force=True)
        mixin._access.remove_worktree.assert_called_once_with("locked-wt", True)  # type: ignore[attr-defined]

    def test_success(self) -> None:
        mixin = _make_mixin(worktree_names=["wt"], lock_exists=False)
        mixin.worktree_remove("wt")
        mixin._access.remove_worktree.assert_called_once_with("wt", False)  # type: ignore[attr-defined]


class TestWorktreeLock:
    """Tests for _WorktreeMixin.worktree_lock()."""

    def test_raises_not_found(self) -> None:
        mixin = _make_mixin(worktree_names=[])
        with pytest.raises(WorktreeNotFoundError):
            mixin.worktree_lock("ghost")

    def test_raises_already_locked(self) -> None:
        mixin = _make_mixin(worktree_names=["wt"], lock_exists=True)
        with pytest.raises(WorktreeLockedError):
            mixin.worktree_lock("wt")

    @patch("coderecon.git.ops_worktree.atomic_write_text")
    def test_success_writes_lock_file(self, mock_write: MagicMock) -> None:
        mixin = _make_mixin(worktree_names=["wt"], lock_exists=False)
        mixin.worktree_lock("wt", reason="maintenance")
        mock_write.assert_called_once()
        _, args, _ = mock_write.mock_calls[0]
        assert args[1] == "maintenance"

    def test_raises_when_gitdir_missing(self) -> None:
        mixin = _make_mixin(worktree_names=["wt"], lock_exists=False)
        mixin._access.worktree_gitdir.return_value.exists.return_value = False  # type: ignore[attr-defined]
        with pytest.raises(WorktreeError, match="Invalid worktree gitdir"):
            mixin.worktree_lock("wt")


class TestWorktreeUnlock:
    """Tests for _WorktreeMixin.worktree_unlock()."""

    def test_raises_not_found(self) -> None:
        mixin = _make_mixin(worktree_names=[])
        with pytest.raises(WorktreeNotFoundError):
            mixin.worktree_unlock("ghost")

    def test_unlocks_when_locked(self) -> None:
        mixin = _make_mixin(worktree_names=["wt"], lock_exists=True)
        mixin.worktree_unlock("wt")
        # lock_file.unlink should be called
        lock_file = mixin._access.worktree_gitdir.return_value.__truediv__.return_value  # type: ignore[attr-defined]
        lock_file.unlink.assert_called_once()

    def test_noop_when_not_locked(self) -> None:
        mixin = _make_mixin(worktree_names=["wt"], lock_exists=False)
        mixin.worktree_unlock("wt")  # should not raise


class TestWorktreePrune:
    """Tests for _WorktreeMixin.worktree_prune()."""

    def test_prunes_stale_entries(self) -> None:
        mixin = _make_mixin(worktree_names=["stale-wt"])
        mixin._access.worktree_is_prunable.return_value = True  # type: ignore[attr-defined]
        result = mixin.worktree_prune()
        assert result == ["stale-wt"]
        mixin._access.git.run.assert_called_with("worktree", "prune")  # type: ignore[attr-defined]

    def test_returns_empty_when_nothing_prunable(self) -> None:
        mixin = _make_mixin(worktree_names=["healthy"])
        mixin._access.worktree_is_prunable.return_value = False  # type: ignore[attr-defined]
        result = mixin.worktree_prune()
        assert result == []


class TestIsWorktreeAndInfo:
    """Tests for is_worktree() and worktree_info()."""

    def test_is_worktree_delegates(self) -> None:
        mixin = _make_mixin(is_worktree=True)
        assert mixin.is_worktree() is True

    def test_worktree_info_returns_none_for_main(self) -> None:
        mixin = _make_mixin(is_worktree=False)
        assert mixin.worktree_info() is None

    def test_worktree_info_returns_info(self) -> None:
        mixin = _make_mixin(is_worktree=True)
        access = mixin._access  # type: ignore[attr-defined]
        access.path = Path("/repo/.worktrees/feat")
        access.is_detached = False
        access.is_unborn = False
        ref_mock = MagicMock()
        ref_mock.shorthand = "feat"
        type(access).head_ref = PropertyMock(return_value=ref_mock)
        access.head_target = "deadbeef"

        info = mixin.worktree_info()
        assert info is not None
        assert info.name == "feat"
        assert info.head_ref == "feat"
        assert info.head_sha == "deadbeef"
        assert info.is_main is False
