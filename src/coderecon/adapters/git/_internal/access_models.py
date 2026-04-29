"""Git data models for repository access."""
from __future__ import annotations

from dataclasses import dataclass

from coderecon.adapters.git._internal.constants import (
    STATUS_INDEX_DELETED,
    STATUS_INDEX_MODIFIED,
    STATUS_INDEX_NEW,
    STATUS_WT_DELETED,
    STATUS_WT_MODIFIED,
    STATUS_WT_NEW,
)


@dataclass(frozen=True, slots=True)
class GitSignature:
    """Lightweight git signature (author/committer)."""
    name: str
    email: str
    time: int  # Unix timestamp
    offset: int  # UTC offset in minutes
@dataclass(frozen=True, slots=True)
class GitCommitData:
    """Parsed commit data from git CLI."""
    sha: str
    tree_sha: str
    parent_shas: tuple[str, ...]
    author: GitSignature
    committer: GitSignature
    message: str
@dataclass(frozen=True, slots=True)
class GitReference:
    """Lightweight reference info."""
    name: str
    target: str  # SHA hex
    shorthand: str
@dataclass(frozen=True, slots=True)
class GitBranchData:
    """Parsed branch data."""
    name: str
    shorthand: str
    target: str  # SHA hex
    upstream: str | None = None
@dataclass(frozen=True, slots=True)
class GitIndexEntry:
    """A single entry in the git index."""
    path: str
    sha: str
    mode: int
@dataclass(frozen=True, slots=True)
class GitStashEntry:
    """A stash entry."""
    message: str
    commit_id: str
@dataclass(frozen=True, slots=True)
class GitTagData:
    """Parsed tag data."""
    name: str
    target_sha: str
    is_annotated: bool
    message: str | None = None
    tagger: GitSignature | None = None

# Status character → flag mapping for --porcelain=v1
_INDEX_STATUS_MAP = {
    "A": STATUS_INDEX_NEW,
    "M": STATUS_INDEX_MODIFIED,
    "D": STATUS_INDEX_DELETED,
    "R": STATUS_INDEX_MODIFIED,  # renamed = modified
    "C": STATUS_INDEX_NEW,  # copied = new
}

_WT_STATUS_MAP = {
    "?": STATUS_WT_NEW,
    "M": STATUS_WT_MODIFIED,
    "D": STATUS_WT_DELETED,
}

