"""Utility functions for the file watcher."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import structlog

from coderecon.core.languages import EXTENSION_TO_NAME
from coderecon.index._internal.ignore import IgnoreChecker

log = structlog.get_logger(__name__)

def collect_watch_dirs(
    repo_root: Path,
    ignore_checker: IgnoreChecker,
) -> list[Path]:
    """Walk the repo tree and collect all directories to watch.

    Respects the directory pruning model implemented by IgnoreChecker:
    - Tier 0 (HARDCODED_DIRS): Always pruned, never watched
    - Tier 1 (DEFAULT_PRUNABLE_DIRS): Pruned unless negated in .reconignore at the repo root
    - Path patterns from .reconignore (e.g. ``ranking/clones/``)

    Returns a flat list of directories. The repo_root itself is always included.
    Each directory gets a single non-recursive inotify watch.
    """
    dirs: list[Path] = [repo_root]
    try:
        for dirpath, dirnames, _filenames in os.walk(repo_root):
            rel_base = os.path.relpath(dirpath, repo_root)
            surviving: list[str] = []
            for d in dirnames:
                if ignore_checker.should_prune_dir(d):
                    continue
                rel_child = os.path.join(rel_base, d) if rel_base != "." else d
                if ignore_checker.should_prune_dir_path(rel_child):
                    continue
                surviving.append(d)
            dirnames[:] = surviving
            for d in dirnames:
                dirs.append(Path(dirpath) / d)
    except OSError:
        log.debug("dir_walk_failed", root=str(repo_root), exc_info=True)
    return dirs

def is_cross_filesystem(path: Path) -> bool:
    """Detect if path is on a cross-filesystem mount (WSL /mnt/*, network drives, etc.)."""
    resolved = path.resolve()
    path_str = str(resolved)
    if (
        path_str.startswith("/mnt/")
        and len(path_str) > 6
        and path_str[5].isalpha()
        and path_str[6] == "/"
    ):
        return True
    return path_str.startswith(("/run/user/", "/media/", "/net/"))

def summarize_changes_by_type(paths: list[Path]) -> str:
    """Summarize file changes by extension/type with grammatical correctness."""
    ext_counts: Counter[str] = Counter()
    for p in paths:
        ext = p.suffix.lower()
        ext_counts[ext] += 1
    parts: list[str] = []
    for ext, count in ext_counts.most_common(3):
        name = EXTENSION_TO_NAME.get(ext, ext.lstrip(".").upper() if ext else "other")
        word = "file" if count == 1 else "files"
        parts.append(f"{count} {name} {word}")
    shown_count = sum(ext_counts[ext] for ext, _ in ext_counts.most_common(3))
    remaining = len(paths) - shown_count
    if remaining > 0:
        word = "other" if remaining == 1 else "others"
        parts.append(f"{remaining} {word}")
    return ", ".join(parts)
