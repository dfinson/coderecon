"""Git index wrapper."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from coderecon.adapters.git._internal.access_models import GitIndexEntry
from coderecon.adapters.git._internal.runner import GitRunner


class GitIndex:
    """Git index operations via subprocess."""
    def __init__(self, git: GitRunner, path: Path) -> None:
        self._git = git
        self._path = path
        self._conflicts: list[tuple[GitIndexEntry | None, GitIndexEntry | None, GitIndexEntry | None]] | None = None
    def add(self, path_or_entry: str | GitIndexEntry) -> None:
        """Stage a file."""
        p = path_or_entry if isinstance(path_or_entry, str) else path_or_entry.path
        self._git.run("add", "--", p)
        self._conflicts = None  # Invalidate
    def remove(self, path: str) -> None:
        """Remove a file from the index."""
        self._git.run("rm", "--cached", "--", path)
        self._conflicts = None
    def write(self) -> None:
        """No-op: subprocess git add/rm are immediate."""
    def read(self) -> None:
        """No-op: subprocess always reads current index."""
    def write_tree(self) -> str:
        """Write current index as a tree object. Returns tree SHA."""
        result = self._git.run("write-tree")
        return result.stdout.strip()
    @property
    def conflicts(self) -> list[tuple[GitIndexEntry | None, GitIndexEntry | None, GitIndexEntry | None]] | None:
        """Get conflict entries, or None if no conflicts."""
        if self._conflicts is not None:
            return self._conflicts if self._conflicts else None
        result = self._git.run("ls-files", "-u")
        if not result.stdout.strip():
            self._conflicts = []
            return None
        # Parse default ls-files -u output: <mode> <sha> <stage>\t<path>
        conflicts_by_path: dict[str, list[GitIndexEntry | None]] = {}
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            meta, path = line.split("\t", 1)
            parts = meta.split()
            if len(parts) < 3:
                continue
            mode, sha, stage = int(parts[0], 8), parts[1], int(parts[2])
            if path not in conflicts_by_path:
                conflicts_by_path[path] = [None, None, None]
            if 1 <= stage <= 3:
                conflicts_by_path[path][stage - 1] = GitIndexEntry(path, sha, mode)
        self._conflicts = [
            (entries[0], entries[1], entries[2]) for entries in conflicts_by_path.values()
        ]
        return self._conflicts if self._conflicts else None
    def diff_to_tree(self, tree_sha: str) -> str:
        """Diff index against a tree. Returns raw diff text."""
        result = self._git.run("diff-index", "-p", "--no-color", tree_sha)
        return result.stdout
    def __contains__(self, path: str) -> bool:
        """Check if path is in the index."""
        rc, stdout, _ = self._git.run_raw("ls-files", "--error-unmatch", "--", path)
        return rc == 0
    def __getitem__(self, path: str) -> GitIndexEntry:
        """Get index entry by path."""
        result = self._git.run("ls-files", "-s", "--", path)
        for line in result.stdout.strip().splitlines():
            # Format: <mode> <sha> <stage>\t<path>
            meta, entry_path = line.split("\t", 1)
            parts = meta.split()
            if entry_path == path:
                return GitIndexEntry(path, parts[1], int(parts[0], 8))
        raise KeyError(path)
    def __len__(self) -> int:
        """Count files in index."""
        result = self._git.run("ls-files")
        lines = result.stdout.strip().splitlines()
        if not lines:
            return 0
        return len(lines) if lines[0] else 0
    def __iter__(self) -> Iterator[GitIndexEntry]:
        """Iterate all index entries."""
        result = self._git.run("ls-files", "-s")
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            meta, path = line.split("\t", 1)
            parts = meta.split()
            yield GitIndexEntry(path, parts[1], int(parts[0], 8))
