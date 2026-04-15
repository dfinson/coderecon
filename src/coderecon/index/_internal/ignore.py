"""Shared ignore/exclude pattern matching with tiered architecture.

Single source of truth for path exclusion logic used by:
- FileWatcher (runtime file change filtering)
- ContextProbe (validation file sampling)
- ContextDiscovery (marker scanning with directory pruning)
- map_repo (filtering results)
- Any component needing .reconignore + .gitignore + UNIVERSAL_EXCLUDES support

Tiered Architecture:
- HARDCODED_DIRS: Always excluded, cannot be overridden (VCS, .recon)
- DEFAULT_PRUNABLE_DIRS: Excluded by default, user can opt-in via !pattern
- .reconignore patterns: User-configurable file/directory patterns

Pattern matching uses ``pathspec`` (gitignore-spec compliant) instead of
``fnmatch`` so that ``*``, ``**``, directory patterns, and negation all
follow the real gitignore specification.
"""

from __future__ import annotations

from pathlib import Path

import pathspec

from coderecon.core.excludes import (
    DEFAULT_PRUNABLE_DIRS,
    HARDCODED_DIRS,
    PRUNABLE_DIRS,
    is_hardcoded_dir,
)

__all__ = [
    "PRUNABLE_DIRS",
    "HARDCODED_DIRS",
    "DEFAULT_PRUNABLE_DIRS",
    "IgnoreChecker",
    "compute_reconignore_hash",
    "discover_reconignore_files",
    "_iter_reconignore_files",
    "matches_glob",
]


class IgnoreChecker:
    """Checks if paths should be ignored based on tiered patterns.

    Tiered Architecture:
    - Tier 0 (HARDCODED_DIRS): Always pruned, not overridable
    - Tier 1 (DEFAULT_PRUNABLE_DIRS): Pruned by default, user can opt-in via !pattern
    - Tier 2 (.reconignore patterns): User-defined patterns with negation support

    Pattern syntax:
    - Standard glob patterns (fnmatch)
    - Directory patterns ending in / match contents
    - Negation with ! prefix (e.g., !vendor/ to opt-in vendor directory)

    .reconignore files themselves are NOT excluded - they need to be indexed
    so file watchers can detect changes and trigger reindexing.
    """

    # Filename for ignore files (like .gitignore but for CodeRecon)
    CPLIGNORE_NAME = ".reconignore"

    @classmethod
    def empty(cls, root: Path) -> IgnoreChecker:
        """Create a checker with base patterns only — no filesystem walk.

        Use with :meth:`load_ignore_file` to build patterns incrementally
        during an existing ``os.walk``, avoiding a redundant tree traversal.
        """
        instance = cls.__new__(cls)
        instance._root = root
        instance._raw_lines = list(DEFAULT_PRUNABLE_DIRS)
        instance._spec: pathspec.PathSpec | None = None
        instance._negated_dirs = set()
        instance._reconignore_paths = []
        return instance

    def load_ignore_file(self, path: Path, prefix: str = "") -> None:
        """Load patterns from a single ignore file.

        Public wrapper around ``_load_ignore_file`` for streaming/
        incremental use during an ``os.walk``.

        Args:
            path: Absolute path to a ``.reconignore`` or ``.gitignore`` file.
            prefix: Relative directory prefix for nested ignore files
                (e.g. ``"src/deep"``).  Root-level files use ``""``.
        """
        if not path.exists():
            return
        self._load_ignore_file(path, prefix)
        if path.name == self.CPLIGNORE_NAME:
            self._reconignore_paths.append(path)

    def __init__(
        self,
        root: Path,
        extra_patterns: list[str] | None = None,
        *,
        respect_gitignore: bool = False,
    ) -> None:
        self._root = root
        # Raw gitignore-syntax lines — compiled lazily into a PathSpec.
        self._raw_lines: list[str] = list(DEFAULT_PRUNABLE_DIRS)
        # Compiled pathspec (invalidated on new pattern loads).
        self._spec: pathspec.PathSpec | None = None
        self._negated_dirs: set[str] = set()  # Track negated directory names for pruning override
        self._reconignore_paths: list[Path] = []  # Track all loaded .reconignore files
        self._load_reconignore_recursive(root)
        if respect_gitignore:
            self._load_gitignore_recursive(root)
        if extra_patterns:
            self._raw_lines.extend(extra_patterns)
            self._spec = None

    @property
    def negated_dirs(self) -> frozenset[str]:
        """Return set of directory names that were negated in .reconignore.

        These directories will NOT be pruned during traversal even if they
        are in DEFAULT_PRUNABLE_DIRS.
        """
        return frozenset(self._negated_dirs)

    @property
    def _compiled(self) -> pathspec.PathSpec:
        """Lazily compile raw lines into a pathspec.PathSpec."""
        if self._spec is None:
            self._spec = pathspec.PathSpec.from_lines("gitignore", self._raw_lines)
        return self._spec

    def should_prune_dir(self, dirname: str) -> bool:
        """Check if a directory should be pruned during traversal.

        Implements tiered pruning:
        - Tier 0 (HARDCODED_DIRS): Always prune, not overridable
        - Tier 1 (DEFAULT_PRUNABLE_DIRS): Prune unless negated in .reconignore

        Args:
            dirname: Directory name (not path), e.g., "node_modules", "vendor"

        Returns:
            True if directory should be skipped during traversal.

        Example:
            # User adds "!vendor/" to .reconignore
            checker.should_prune_dir("vendor")  # Returns False (opted-in)
            checker.should_prune_dir(".git")    # Returns True (hardcoded)
            checker.should_prune_dir("node_modules")  # Returns True (default)
        """
        # Tier 0: Hardcoded dirs are ALWAYS pruned
        if is_hardcoded_dir(dirname):
            return True

        # Tier 1: Default prunable dirs, unless user negated them
        if dirname in DEFAULT_PRUNABLE_DIRS:
            return dirname not in self._negated_dirs

        # Not in any prunable set
        return False

    def should_prune_dir_path(self, rel_dir_path: str) -> bool:
        """Check if a directory path should be pruned based on .reconignore patterns.

        Unlike ``should_prune_dir`` (which only checks bare directory names
        against the hardcoded/default sets), this method checks the full
        relative path against all loaded .reconignore and .gitignore patterns.

        This ensures that user-defined directory exclusions like
        ``ranking/clones/`` actually prevent the walker from descending
        into those directories rather than just filtering their files.

        Args:
            rel_dir_path: Relative directory path in POSIX format
                (e.g. ``"ranking/clones"``).

        Returns:
            True if directory should be pruned.
        """
        if not rel_dir_path or rel_dir_path == ".":
            return False

        # pathspec match_file works on the path as-is; adding trailing /
        # lets directory-only patterns (e.g. "build/") match correctly.
        return self._compiled.match_file(rel_dir_path) or self._compiled.match_file(
            rel_dir_path + "/"
        )

    @property
    def reconignore_paths(self) -> list[Path]:
        """Return list of all .reconignore files that were loaded.

        Used by Reconciler to track hashes and detect changes.
        """
        return self._reconignore_paths.copy()

    def compute_combined_hash(self) -> str | None:
        """Compute combined hash of all .reconignore file contents.

        Returns a hash that changes if ANY .reconignore file changes.
        Returns None if no .reconignore files exist.

        Used by Reconciler to detect .reconignore changes and trigger reindex.
        """
        import hashlib

        if not self._reconignore_paths:
            return None

        hasher = hashlib.sha256()
        # Sort paths for deterministic ordering
        for path in sorted(self._reconignore_paths):
            try:
                content = path.read_bytes()
                # Include path in hash so moving files is detected
                hasher.update(str(path).encode())
                hasher.update(content)
            except OSError:
                # File was deleted between loading and hashing
                hasher.update(str(path).encode())
                hasher.update(b"__DELETED__")
        return hasher.hexdigest()

    def _load_reconignore_recursive(self, root: Path) -> None:
        """Load .reconignore from root and all subdirectories.

        Handles nested .reconignore files by prefixing patterns with their
        relative directory path (same behavior as .gitignore).
        """
        for reconignore_path, prefix in _iter_reconignore_files(root):
            self._load_ignore_file(reconignore_path, prefix=prefix)
            self._reconignore_paths.append(reconignore_path)

    def _load_gitignore_recursive(self, root: Path) -> None:
        """Load .gitignore from root and all subdirectories.

        Handles nested .gitignore files by prefixing patterns with their
        relative directory path.
        """
        # Load root .gitignore
        root_gitignore = root / ".gitignore"
        if root_gitignore.exists():
            self._load_ignore_file(root_gitignore)

        # Walk for nested .gitignore files
        for dirpath, dirnames, filenames in root.walk():
            # Skip prunable dirs
            dirnames[:] = [d for d in dirnames if d not in PRUNABLE_DIRS]

            if dirpath == root:
                continue  # Already loaded

            if ".gitignore" in filenames:
                gitignore_path = dirpath / ".gitignore"
                rel_dir = dirpath.relative_to(root)
                self._load_ignore_file(gitignore_path, prefix=str(rel_dir))

    def _load_ignore_file(self, path: Path, prefix: str = "") -> None:
        """Load patterns from an ignore file.

        Args:
            path: Path to the ignore file
            prefix: Directory prefix for nested .gitignore patterns

        Also tracks negated directory names (e.g., !vendor/) to allow
        opting-in to directories that are pruned by default.
        """
        try:
            content = path.read_text()
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Handle negation
                is_negation = line.startswith("!")
                if is_negation:
                    line = line[1:]

                # Track negated directory names for pruning override
                # e.g., "!vendor/" or "!vendor" -> track "vendor" as negated
                if is_negation and not prefix:
                    # Only track root-level negations for dir pruning
                    dir_name = line.rstrip("/")
                    if dir_name and "/" not in dir_name and "*" not in dir_name:
                        self._negated_dirs.add(dir_name)

                # Keep the pattern in gitignore-native format — pathspec
                # handles trailing-/ (directory) and ** semantics correctly.
                pattern = line

                # Apply prefix for nested .gitignore / .reconignore
                if prefix:
                    pattern = f"{prefix}/{pattern}"

                # Re-add negation prefix
                if is_negation:
                    pattern = f"!{pattern}"

                self._raw_lines.append(pattern)

            # Invalidate compiled spec so it is rebuilt on next match.
            self._spec = None
        except OSError:
            pass

    def should_ignore(self, path: Path) -> bool:
        try:
            rel_path = path.relative_to(self._root)
        except ValueError:
            return True

        # Normalize to POSIX-style separators for pattern matching on Windows
        rel_str = rel_path.as_posix()

        return self._compiled.match_file(rel_str)

    def is_excluded_rel(self, rel_path: str) -> bool:
        # Normalize to POSIX-style separators for pattern matching on Windows
        rel_path_posix = rel_path.replace("\\", "/")

        return self._compiled.match_file(rel_path_posix)


def matches_glob(rel_path: str, pattern: str) -> bool:
    """Check if a path matches a glob pattern (gitignore-spec compliant)."""
    spec = pathspec.PathSpec.from_lines("gitignore", [pattern])
    return spec.match_file(rel_path)


def _iter_reconignore_files(root: Path) -> list[tuple[Path, str]]:
    """Find all .reconignore files with their relative prefix.

    Returns list of (path, prefix) tuples where prefix is used for
    pattern scoping (empty string for root-level files).

    Shared by IgnoreChecker._load_reconignore_recursive and discover_reconignore_files.
    """
    results: list[tuple[Path, str]] = []

    # Legacy location (root-scoped)
    legacy_path = root / ".recon" / IgnoreChecker.CPLIGNORE_NAME
    if legacy_path.exists():
        results.append((legacy_path, ""))

    # Root .reconignore (root-scoped)
    root_reconignore = root / IgnoreChecker.CPLIGNORE_NAME
    if root_reconignore.exists():
        results.append((root_reconignore, ""))

    # Walk for nested .reconignore files
    for dirpath, dirnames, filenames in root.walk():
        # Skip prunable dirs (but allow walking into .recon)
        dirnames[:] = [d for d in dirnames if d not in PRUNABLE_DIRS or d == ".recon"]

        # Skip root and .recon (already handled above)
        if dirpath == root or dirpath == root / ".recon":
            continue

        if IgnoreChecker.CPLIGNORE_NAME in filenames:
            rel_dir = str(dirpath.relative_to(root))
            results.append((dirpath / IgnoreChecker.CPLIGNORE_NAME, rel_dir))

    return results


def discover_reconignore_files(root: Path) -> list[Path]:
    """Walk tree to find all .reconignore files.

    Lightweight alternative to constructing a full IgnoreChecker when
    only file discovery (not pattern matching) is needed.
    """
    return [path for path, _ in _iter_reconignore_files(root)]


def compute_reconignore_hash(root: Path) -> str | None:
    """Compute combined hash of all .reconignore files without loading patterns.

    Returns a hash that changes if ANY .reconignore file changes.
    Returns None if no .reconignore files exist.
    """
    import hashlib

    paths = discover_reconignore_files(root)
    if not paths:
        return None

    hasher = hashlib.sha256()
    for path in sorted(paths):
        try:
            content = path.read_bytes()
            hasher.update(str(path).encode())
            hasher.update(content)
        except OSError:
            hasher.update(str(path).encode())
            hasher.update(b"__DELETED__")
    return hasher.hexdigest()
