"""Worktree operations mixin for GitOps."""
from __future__ import annotations

from pathlib import Path

import structlog

from coderecon.files.ops import atomic_write_text
from coderecon.git.errors import (
    BranchNotFoundError,
    GitError,
    WorktreeError,
    WorktreeExistsError,
    WorktreeLockedError,
    WorktreeNotFoundError,
)
from coderecon.git.models import WorktreeInfo

log = structlog.get_logger(__name__)


class _WorktreeMixin:
    """Mixin providing worktree operations for GitOps."""

    def worktrees(self) -> list[WorktreeInfo]:
        """List all worktrees including main working directory."""
        result: list[WorktreeInfo] = []
        # Parse git worktree list --porcelain for complete info
        wt_output = self._access.git.run("worktree", "list", "--porcelain")
        entries: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in wt_output.stdout.splitlines():
            if not line:
                if current:
                    entries.append(current)
                    current = {}
                continue
            if line.startswith("worktree "):
                current["path"] = line[len("worktree "):]
            elif line.startswith("HEAD "):
                current["head"] = line[len("HEAD "):]
            elif line.startswith("branch "):
                current["branch"] = line[len("branch "):]
            elif line == "bare":
                current["bare"] = "true"
            elif line == "detached":
                current["detached"] = "true"
        if current:
            entries.append(current)
        for i, entry in enumerate(entries):
            is_main = i == 0
            wt_path = entry.get("path", "")
            head_sha = entry.get("head", "")
            branch_ref = entry.get("branch", "")
            is_detached = "detached" in entry
            is_bare = "bare" in entry
            if branch_ref:
                head_ref_name = branch_ref.removeprefix("refs/heads/")
            elif is_detached:
                head_ref_name = "HEAD"
            else:
                head_ref_name = ""
            name = "main" if is_main else Path(wt_path).name
            # Check lock status
            is_locked = False
            lock_reason = None
            if not is_main and name:
                lock_file = self._access.worktree_gitdir(name) / "locked"
                if lock_file.exists():
                    is_locked = True
                    lock_reason = lock_file.read_text().strip() or None
            is_prunable = not is_main and wt_path and not Path(wt_path).exists()
            result.append(
                WorktreeInfo(
                    name=name,
                    path=wt_path,
                    head_ref=head_ref_name,
                    head_sha=head_sha,
                    is_main=is_main,
                    is_bare=is_bare,
                    is_locked=is_locked,
                    lock_reason=lock_reason,
                    is_prunable=is_prunable,
                )
            )
        return result
    def worktree_add(self, path: Path, ref: str, checkout: bool = True) -> GitOps:  # noqa: ARG002
        """Add worktree at path for ref. Returns GitOps for new worktree."""
        if not self._access.has_local_branch(ref):
            raise BranchNotFoundError(ref)
        if path.exists():
            raise WorktreeError(f"Path already exists: {path}")
        name = path.name
        existing_names = self._access.list_worktrees()
        if name in existing_names:
            raise WorktreeExistsError(name)
        for wt_name in existing_names:
            try:
                wt_path = self._access.worktree_path(wt_name)
                if Path(wt_path).resolve() == path.resolve():
                    raise WorktreeError(f"Path already in use by worktree '{wt_name}'")
            except GitError:
                log.debug("worktree_path_check_failed", worktree=wt_name, exc_info=True)
        self._access.add_worktree(name, str(path), ref)
        from coderecon.git.ops import GitOps
        return GitOps(path)
    def worktree_open(self, name: str) -> GitOps:
        """Get GitOps instance for existing worktree by name."""
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)
        wt_path = self._access.worktree_path(name)
        from coderecon.git.ops import GitOps
        return GitOps(wt_path)
    def worktree_remove(self, name: str, force: bool = False) -> None:
        """Remove worktree."""
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)
        lock_file = self._access.worktree_gitdir(name) / "locked"
        if lock_file.exists() and not force:
            raise WorktreeLockedError(name)
        try:
            self._access.remove_worktree(name, force)
        except (OSError, subprocess.SubprocessError) as e:
            raise WorktreeError(f"Failed to remove worktree '{name}': {e}") from e
    def worktree_lock(self, name: str, reason: str | None = None) -> None:
        """Lock worktree to prevent pruning."""
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)
        gitdir = self._access.worktree_gitdir(name)
        if not gitdir.exists():
            raise WorktreeError(f"Invalid worktree gitdir (missing): {gitdir}")
        lock_file = gitdir / "locked"
        if lock_file.exists():
            raise WorktreeLockedError(name)
        try:
            atomic_write_text(lock_file, reason or "")
        except OSError as e:
            raise WorktreeError(f"Failed to lock worktree {name}: {e}") from e
    def worktree_unlock(self, name: str) -> None:
        """Unlock worktree."""
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)
        lock_file = self._access.worktree_gitdir(name) / "locked"
        if lock_file.exists():
            lock_file.unlink()
    def worktree_prune(self) -> list[str]:
        """Remove stale worktree entries. Returns pruned names."""
        pruned = []
        for name in self._access.list_worktrees():
            if self._access.worktree_is_prunable(name):
                self._access.git.run("worktree", "prune")
                pruned.append(name)
                break  # Prune removes all stale at once
        return pruned
    def is_worktree(self) -> bool:
        """True if this GitOps is for a worktree (not main working directory)."""
        return self._access.is_worktree()
    def worktree_info(self) -> WorktreeInfo | None:
        """Get info about this worktree, or None if main working directory."""
        if not self.is_worktree():
            return None
        ref = self._access.head_ref
        return WorktreeInfo(
            name=self._access.path.name,
            path=str(self._access.path),
            head_ref=ref.shorthand if not self._access.is_detached else "HEAD",
            head_sha=self._access.head_target if not self._access.is_unborn else "",
            is_main=False,
            is_bare=False,
            is_locked=False,
            lock_reason=None,
            is_prunable=False,
        )
    # Submodule Operations
