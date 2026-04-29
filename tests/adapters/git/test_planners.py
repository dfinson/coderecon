"""Tests for planner modules."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coderecon.adapters.git import DiffInfo, GitOps, RefNotFoundError

class TestDiffPlanner:
    """Tests for diff planning edge cases."""

    def test_diff_staged_normal(self, repo_with_uncommitted: Path) -> None:
        """Staged diff on normal repo should work."""
        ops = GitOps(repo_with_uncommitted)

        diff = ops.diff(staged=True)

        assert isinstance(diff, DiffInfo)

    def test_diff_ref_to_working(self, repo_with_uncommitted: Path) -> None:
        """Diff from ref to working tree should work."""
        ops = GitOps(repo_with_uncommitted)

        diff = ops.diff(base="HEAD")

        assert isinstance(diff, DiffInfo)

    def test_diff_staged_unborn_repo(self, tmp_path: Path) -> None:
        """Staged diff on unborn repo should work."""
        repo_path = tmp_path / "unborn"
        repo_path.mkdir()
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)

        ops = GitOps(repo_path)
        (repo_path / "new.txt").write_text("content\n")
        ops.stage(["new.txt"])

        diff = ops.diff(staged=True)

        assert isinstance(diff, DiffInfo)

    def test_diff_base_on_unborn_repo_raises(self, tmp_path: Path) -> None:
        """Diff with base on unborn repo should raise."""
        repo_path = tmp_path / "unborn"
        repo_path.mkdir()
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)

        ops = GitOps(repo_path)

        with pytest.raises(RefNotFoundError):
            ops.diff(base="HEAD")

class TestCheckoutPlanner:
    """Tests for checkout planning edge cases."""

    def test_checkout_detached_by_sha(self, temp_repo: Path) -> None:
        """Checking out by SHA should result in detached HEAD."""
        ops = GitOps(temp_repo)
        head_commit = ops.head_commit()
        assert head_commit is not None
        head_sha = head_commit.sha

        ops.checkout(head_sha)

        # Verify detached
        assert ops.current_branch() is None

    def test_checkout_local_branch(self, repo_with_branches: Path) -> None:
        """Checking out local branch should work."""
        ops = GitOps(repo_with_branches)

        ops.checkout("feature")

        assert ops.current_branch() == "feature"

class TestRebasePlanner:
    """Tests for rebase planning edge cases."""

    def test_rebase_plan_with_commits(
        self, git_repo_with_commits: tuple[Path, GitOps, list[str]]
    ) -> None:
        """Rebase plan with commits should list them."""
        repo_path, ops, shas = git_repo_with_commits

        # Get current branch name
        current = ops.current_branch()
        assert current is not None

        # Plan rebase onto first commit
        ops.checkout(shas[0])
        ops.create_branch("base")
        ops.checkout(current)

        plan = ops.rebase_plan("base")

        assert len(plan.steps) > 0

class TestCommitRangeParsing:
    """Tests for commit range in diff."""

    def test_diff_with_base_and_target(self, repo_with_history: Path) -> None:
        """Diff with base and target should work."""
        ops = GitOps(repo_with_history)
        log = ops.log(limit=3)

        diff = ops.diff(base=log[2].sha, target=log[0].sha)

        assert isinstance(diff, DiffInfo)

    def test_diff_single_base(self, repo_with_history: Path) -> None:
        """Diff from single base to working tree."""
        ops = GitOps(repo_with_history)
        log = ops.log(limit=2)

        diff = ops.diff(base=log[1].sha)

        assert isinstance(diff, DiffInfo)
