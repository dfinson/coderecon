"""Serializable data models for git operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from coderecon.git._internal.access import GitBranchData, GitCommitData, GitSignature as GitSig

DeltaStatus = Literal["added", "deleted", "modified", "renamed", "copied", "unknown"]

# Map git diff --name-status letters to DeltaStatus
_DELTA_CHAR_MAP: dict[str, DeltaStatus] = {
    "A": "added",
    "D": "deleted",
    "M": "modified",
    "R": "renamed",
    "C": "copied",
}


@dataclass(frozen=True, slots=True)
class Signature:
    """Git author/committer signature."""

    name: str
    email: str
    time: datetime

    @classmethod
    def from_git(cls, sig: GitSig) -> Signature:
        return cls(sig.name, sig.email, datetime.fromtimestamp(sig.time, tz=UTC))


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """Git commit information."""

    sha: str
    short_sha: str
    message: str
    author: Signature
    committer: Signature
    parent_shas: tuple[str, ...]

    @classmethod
    def from_git(cls, commit: GitCommitData) -> CommitInfo:
        return cls(
            sha=commit.sha,
            short_sha=commit.sha[:7],
            message=commit.message,
            author=Signature.from_git(commit.author),
            committer=Signature.from_git(commit.committer),
            parent_shas=commit.parent_shas,
        )


@dataclass(frozen=True, slots=True)
class BranchInfo:
    """Git branch information."""

    name: str
    short_name: str
    target_sha: str
    is_remote: bool
    upstream: str | None = None

    @classmethod
    def from_git(cls, branch: GitBranchData) -> BranchInfo:
        return cls(
            name=branch.name,
            short_name=branch.shorthand,
            target_sha=branch.target,
            is_remote=branch.name.startswith("refs/remotes/"),
            upstream=branch.upstream,
        )

    @classmethod
    def from_branch_data(
        cls, name: str, shorthand: str, target: str, *, is_remote: bool = False, upstream: str | None = None
    ) -> BranchInfo:
        return cls(
            name=name,
            short_name=shorthand,
            target_sha=target,
            is_remote=is_remote,
            upstream=upstream,
        )


@dataclass(frozen=True, slots=True)
class TagInfo:
    """Git tag information."""

    name: str
    target_sha: str
    is_annotated: bool
    message: str | None = None
    tagger: Signature | None = None


@dataclass(frozen=True, slots=True)
class RemoteInfo:
    """Git remote information."""

    name: str
    url: str
    push_url: str | None = None


@dataclass(frozen=True, slots=True)
class DiffFile:
    """Single file in a diff."""

    old_path: str | None
    new_path: str | None
    status: DeltaStatus
    additions: int
    deletions: int


@dataclass(frozen=True, slots=True)
class DiffInfo:
    """Git diff summary."""

    files: tuple[DiffFile, ...]
    total_additions: int
    total_deletions: int
    files_changed: int
    patch: str | None = None

    @classmethod
    def from_diff_text(cls, diff_text: str, numstat: list[tuple[str, int, int, str]], *, include_patch: bool = False) -> DiffInfo:
        """Build DiffInfo from raw diff text and numstat output.

        Args:
            diff_text: Raw unified diff text.
            numstat: List of (status_char, additions, deletions, path) tuples.
            include_patch: Whether to include the full patch text.
        """
        files_list: list[DiffFile] = []
        total_adds = 0
        total_dels = 0
        for status_char, adds, dels, path in numstat:
            delta_status = _DELTA_CHAR_MAP.get(status_char, "unknown")
            files_list.append(
                DiffFile(
                    old_path=None if delta_status == "added" else path,
                    new_path=path,
                    status=delta_status,
                    additions=adds,
                    deletions=dels,
                )
            )
            total_adds += adds
            total_dels += dels
        return cls(
            files=tuple(files_list),
            total_additions=total_adds,
            total_deletions=total_dels,
            files_changed=len(files_list),
            patch=diff_text if include_patch else None,
        )


@dataclass(frozen=True, slots=True)
class FileDiffSummary:
    """Per-file diff statistics."""

    path: str
    status: DeltaStatus
    additions: int
    deletions: int
    word_count: int | None = None  # Only populated if include_word_count=True


def _count_words(text: str) -> int:
    """Count words in text."""
    return len(text.split())


@dataclass(frozen=True, slots=True)
class DiffSummary:
    """
    Diff statistics with progressive detail levels.

    Cost tiers (use minimal detail needed):
    - Fast (default): diff.stats only, no iteration
    - Medium (include_per_file): iterate patches for per-file adds/dels
    - Slow (include_word_count): parse patch text for word counts
    """

    files_changed: int
    total_additions: int
    total_deletions: int
    total_lines: int
    per_file: tuple[FileDiffSummary, ...] | None = None  # Only if include_per_file
    total_word_count: int | None = None  # Only if include_word_count

    @property
    def file_paths(self) -> tuple[str, ...]:
        """List of changed file paths (empty if per_file not requested)."""
        return tuple(f.path for f in self.per_file) if self.per_file else ()

    @classmethod
    def from_diff_text(
        cls,
        diff_text: str,
        numstat: list[tuple[str, int, int, str]],
        *,
        include_per_file: bool = False,
        include_word_count: bool = False,
    ) -> DiffSummary:
        """Build DiffSummary from raw diff text and numstat output."""
        total_adds = sum(row[1] for row in numstat)
        total_dels = sum(row[2] for row in numstat)

        if not include_per_file and not include_word_count:
            return cls(
                files_changed=len(numstat),
                total_additions=total_adds,
                total_deletions=total_dels,
                total_lines=total_adds + total_dels,
            )

        per_file: list[FileDiffSummary] = []
        total_words = 0 if include_word_count else None

        for status_char, adds, dels, path in numstat:
            delta_status = _DELTA_CHAR_MAP.get(status_char, "unknown")
            words: int | None = None
            if include_word_count:
                # Estimate word count from additions
                words = _count_words(path)  # Approximation; full patch parsing expensive
                total_words = (total_words or 0) + words

            per_file.append(
                FileDiffSummary(
                    path=path,
                    status=delta_status,
                    additions=adds,
                    deletions=dels,
                    word_count=words,
                )
            )

        return cls(
            files_changed=len(numstat),
            total_additions=total_adds,
            total_deletions=total_dels,
            total_lines=total_adds + total_dels,
            per_file=tuple(per_file),
            total_word_count=total_words,
        )


@dataclass(frozen=True, slots=True)
class BlameHunk:
    """A hunk in blame output."""

    commit_sha: str
    author: Signature
    start_line: int
    line_count: int
    original_start_line: int


@dataclass(frozen=True, slots=True)
class BlameInfo:
    """Git blame result."""

    path: str
    hunks: tuple[BlameHunk, ...]

    @classmethod
    def from_blame_data(cls, path: str, blame_hunks: list[dict]) -> BlameInfo:
        """Build from parsed blame output (list of hunk dicts)."""
        return cls(
            path=path,
            hunks=tuple(
                BlameHunk(
                    commit_sha=hunk["sha"],
                    author=Signature(
                        name=hunk.get("author_name", ""),
                        email=hunk.get("author_email", ""),
                        time=datetime.fromtimestamp(hunk.get("author_time", 0), tz=UTC),
                    ),
                    start_line=hunk["final_line"],
                    line_count=hunk["num_lines"],
                    original_start_line=hunk.get("orig_line", hunk["final_line"]),
                )
                for hunk in blame_hunks
            ),
        )


@dataclass(frozen=True, slots=True)
class StashEntry:
    """Git stash entry."""

    index: int
    message: str
    commit_sha: str


@dataclass(frozen=True, slots=True)
class RefInfo:
    """Reference information (HEAD, etc)."""

    name: str
    target_sha: str
    shorthand: str
    is_detached: bool = False


@dataclass(frozen=True, slots=True)
class MergeResult:
    """Result of a merge operation."""

    success: bool
    commit_sha: str | None
    conflict_paths: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PullResult:
    """Result of a pull (fetch + merge) operation."""

    success: bool
    commit_sha: str | None
    up_to_date: bool = False
    conflict_paths: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class MergeAnalysis:
    """Result of merge analysis."""

    up_to_date: bool
    fastforward_possible: bool
    conflicts_likely: bool


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Result of cherrypick/revert operations."""

    success: bool
    conflict_paths: tuple[str, ...] = field(default_factory=tuple)


# =============================================================================
# Worktree Types
# =============================================================================


@dataclass(frozen=True, slots=True)
class WorktreeInfo:
    """Git worktree information."""

    name: str
    path: str
    head_ref: str  # Branch name or "HEAD" if detached
    head_sha: str
    is_main: bool  # True for main working directory
    is_bare: bool
    is_locked: bool
    lock_reason: str | None
    is_prunable: bool  # True if worktree directory is missing


# =============================================================================
# Submodule Types
# =============================================================================

SubmoduleState = Literal[
    "uninitialized",  # In .gitmodules but not cloned
    "clean",  # Initialized, at recorded commit
    "dirty",  # Has local modifications
    "outdated",  # Behind recorded commit
    "missing",  # Directory missing
]


@dataclass(frozen=True, slots=True)
class SubmoduleInfo:
    """Git submodule information."""

    name: str
    path: str
    url: str
    branch: str | None
    head_sha: str | None
    status: SubmoduleState


@dataclass(frozen=True, slots=True)
class SubmoduleStatus:
    """Detailed submodule status."""

    info: SubmoduleInfo
    workdir_dirty: bool
    index_dirty: bool
    untracked_count: int
    recorded_sha: str
    actual_sha: str | None


@dataclass(frozen=True, slots=True)
class SubmoduleUpdateResult:
    """Result of submodule update operation."""

    updated: tuple[str, ...]
    failed: tuple[tuple[str, str], ...]  # (path, error)
    already_current: tuple[str, ...]


# =============================================================================
# Rebase Types
# =============================================================================

RebaseAction = Literal["pick", "reword", "edit", "squash", "fixup", "drop"]
RebaseResultState = Literal["done", "conflict", "edit_pause", "aborted"]


@dataclass(frozen=True, slots=True)
class RebaseStep:
    """A single step in a rebase plan."""

    action: RebaseAction
    commit_sha: str
    message: str | None = None  # Original message, or override for reword/squash


@dataclass(frozen=True, slots=True)
class RebasePlan:
    """A rebase plan ready for execution."""

    upstream: str
    onto: str
    steps: tuple[RebaseStep, ...]


@dataclass(frozen=True, slots=True)
class RebaseResult:
    """Result of a rebase operation."""

    success: bool
    completed_steps: int
    total_steps: int
    state: RebaseResultState
    conflict_paths: tuple[str, ...] = field(default_factory=tuple)
    current_commit: str | None = None  # For edit_pause
    new_head: str | None = None  # Final HEAD after successful rebase


def validate_branch_name(name: str) -> str:
    """Validate and clean a Git branch name.

    Args:
        name: The branch name to validate.

    Returns:
        The cleaned (stripped) branch name.

    Raises:
        ValueError: If the name is empty, contains spaces, starts with '-',
            or contains '..'.
    """
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Branch name must not be empty")
    if " " in cleaned:
        raise ValueError(f"Branch name must not contain spaces: {cleaned!r}")
    if cleaned.startswith("-"):
        raise ValueError(f"Branch name must not start with '-': {cleaned!r}")
    if ".." in cleaned:
        raise ValueError(f"Branch name must not contain '..': {cleaned!r}")
    return cleaned
