"""Precondition helpers and HEAD state policy for git operations."""

from __future__ import annotations

from coderecon.git._internal.access import RepoAccess
from coderecon.git.errors import (
    BranchNotFoundError,
    DetachedHeadError,
    GitError,
    NothingToCommitError,
)

# Branch Preconditions


def require_not_unborn(access: RepoAccess, operation: str) -> None:
    """Raise if HEAD is unborn (no commits yet)."""
    if access.is_unborn:
        raise GitError(f"Cannot {operation}: no commits yet")


def require_current_branch(access: RepoAccess, operation: str) -> str:
    """Raise if detached HEAD; return current branch name."""
    branch = access.current_branch_name()
    if not branch:
        raise DetachedHeadError(operation)
    return branch


def require_not_current_branch(access: RepoAccess, branch_name: str) -> None:
    """Raise if trying to operate on current branch."""
    if branch_name == access.current_branch_name():
        raise GitError(f"Cannot delete current branch: {branch_name}")


def require_branch_exists(access: RepoAccess, branch_name: str) -> None:
    """Raise if branch doesn't exist."""
    if not access.has_local_branch(branch_name):
        raise BranchNotFoundError(branch_name)


# Unborn HEAD Policy - centralized rules for unborn state


def check_nothing_to_commit(access: RepoAccess, allow_empty: bool) -> None:
    """Check if there's something to commit, respecting unborn state."""
    if allow_empty:
        return

    if access.is_unborn:
        if len(access.index) == 0:
            raise NothingToCommitError
    else:
        # Check staged diff via numstat (faster than parsing full diff text)
        numstat = access.diff_numstat("--cached")
        if len(numstat) == 0:
            raise NothingToCommitError
