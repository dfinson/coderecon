"""File operations module — internal utilities for path validation and file access."""

from coderecon.adapters.files.ops import (
    FileEntry,
    FileOps,
    FileResult,
    ListFilesResult,
    ReadFilesResult,
    atomic_write_text,
)

__all__ = [
    "FileEntry",
    "FileOps",
    "FileResult",
    "ListFilesResult",
    "ReadFilesResult",
    "atomic_write_text",
]
