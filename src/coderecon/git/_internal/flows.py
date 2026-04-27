"""Reusable transactional patterns for write operations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from coderecon.git._internal.access import GitSignature, RepoAccess

@dataclass(frozen=True, slots=True)
class ConflictCheckResult:
    """Result of an operation that may produce conflicts."""

    has_conflicts: bool
    conflict_paths: tuple[str, ...]

class WriteFlows:
    """Reusable transactional patterns for git write operations."""

    def __init__(self, access: RepoAccess) -> None:
        self._access = access

    def extract_conflict_paths(self) -> tuple[str, ...]:
        """Extract unique conflict paths from index."""
        conflicts = self._access.index.conflicts
        if not conflicts:
            return ()
        paths: set[str] = set()
        for ancestor, ours, theirs in conflicts:
            for entry in (ancestor, ours, theirs):
                if entry:
                    paths.add(entry.path)
        return tuple(sorted(paths))

    def check_conflicts(self) -> ConflictCheckResult:
        """Check if index has conflicts and extract paths."""
        if self._access.index.conflicts:
            return ConflictCheckResult(True, self.extract_conflict_paths())
        return ConflictCheckResult(False, ())

    def write_tree_and_commit(
        self,
        message: str,
        parents: list[str],
        *,
        author: GitSignature | None = None,
    ) -> str:
        """Write index tree and create commit. Returns sha."""
        tree_sha = self._access.index.write_tree()
        sig = self._access.default_signature
        oid = self._access.create_commit(
            "HEAD",
            author or sig,
            sig,
            message,
            tree_sha,
            parents,
        )
        return oid

    def commit_from_index(self, message: str) -> str:
        """Create commit from current index state. Returns sha."""
        parents = [] if self._access.is_unborn else [self._access.head_target]
        return self.write_tree_and_commit(message, parents)

    @contextmanager
    def stateful_op(self) -> Iterator[None]:
        """Context manager that guarantees state cleanup after stateful operations."""
        try:
            yield
        finally:
            self._access.state_cleanup()

    def run_merge_like_operation(
        self,
        operation_fn: callable,
        message: str,
        parents: list[str],
        *,
        author: GitSignature | None = None,
    ) -> tuple[bool, str | None, tuple[str, ...]]:
        """Run merge-like operation with guaranteed cleanup.

        Returns (success, commit_sha_or_none, conflict_paths).
        """
        with self.stateful_op():
            operation_fn()
            conflicts = self.check_conflicts()
            if conflicts.has_conflicts:
                return False, None, conflicts.conflict_paths

            sha = self.write_tree_and_commit(message, parents, author=author)
            return True, sha, ()
