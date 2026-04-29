"""Glob pattern matching utilities for index operations."""

from __future__ import annotations

import re
from functools import lru_cache


def _glob_to_regex(pattern: str) -> str:
    """Convert a glob pattern to a regex string.
    Handles ``**`` (zero or more directories), ``*`` (non-separator chars),
    ``?`` (single non-separator char), and ``[...]`` character classes.
    Anchoring rules (matching PurePosixPath.match semantics):
    - Patterns starting with ``/`` are absolute (full-path match).
    - Patterns starting with ``**/`` already anchor via ``(?:.+/)?``.
    - Other patterns with ``/`` are right-anchored (match from the right).
    - Bare patterns (no ``/``) match the last path component.
    Unlike PurePosixPath.match in Python < 3.12, ``**`` is correctly treated
    as zero-or-more directory segments, not a single ``*``.
    """
    has_slash = "/" in pattern
    starts_dstar = pattern.startswith("**/") or pattern == "**"
    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    parts.append("(?:.+/)?")  # **/ = zero or more dirs
                    i += 3
                else:
                    parts.append(".*")  # ** at end = everything
                    i += 2
            else:
                parts.append("[^/]*")  # * = any non-separator
                i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        elif c == "[":
            # Character class — find closing ]
            j = i + 1
            if j < n and pattern[j] in ("!", "^"):
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            bracket = pattern[i : j + 1]
            # Convert [!...] negation to regex [^...] negation
            if len(bracket) > 2 and bracket[1] == "!":
                bracket = "[^" + bracket[2:]
            parts.append(bracket)
            i = j + 1
        else:
            parts.append(re.escape(c))
            i += 1
    body = "".join(parts)
    if pattern.startswith("/"):
        # Absolute pattern — full match from root
        return "^" + body + "$"
    elif starts_dstar:
        # ** already provides anchoring via (?:.+/)?
        return "^" + body + "$"
    elif has_slash:
        # Relative with / — right-anchored (PurePosixPath compat)
        return "(?:^|.*/)" + body + "$"
    else:
        # Bare filename/glob — match last path component
        return "(?:^|/)" + body + "$"


@lru_cache(maxsize=512)
def _compile_glob_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a single glob pattern to a regex.  LRU-cached by pattern string."""
    return re.compile(_glob_to_regex(pattern))


def _compile_glob_set(patterns: list[str]) -> re.Pattern[str] | None:
    """Compile a list of glob patterns into a single combined regex.
    Returns ``None`` if *patterns* is empty.  The combined regex matches if
    ANY individual pattern matches — equivalent to iterating with
    ``_matches_glob`` and returning on first hit, but in a single
    ``re.search`` call.
    """
    if not patterns:
        return None
    return _compile_glob_set_cached(tuple(patterns))


@lru_cache(maxsize=128)
def _compile_glob_set_cached(patterns: tuple[str, ...]) -> re.Pattern[str]:
    """Cache-friendly compile for a frozen tuple of patterns."""
    alternatives = [_glob_to_regex(p) for p in patterns]
    combined = "|".join(f"(?:{alt})" for alt in alternatives)
    return re.compile(combined)


def _matches_glob(rel_path: str, pattern: str) -> bool:
    """Check if a path matches a glob pattern, with ``**`` support.
    Uses pre-compiled regex (≈82× faster than PurePosixPath.match).
    Handles ``**`` as zero-or-more directory segments correctly — unlike
    PurePosixPath.match in Python < 3.12, which treats ``**`` as ``*``.
    """
    if not pattern:
        return not rel_path  # empty pattern matches only empty path
    if not rel_path:
        return False
    return bool(_compile_glob_pattern(pattern).search(rel_path))


def _matches_filter_paths(rel_path: str, filter_paths: list[str]) -> bool:
    """Check if a path matches any of the filter_paths patterns.
    Supports:
    - Directory prefix matching: "src/" or "src" matches all files under src/
    - Exact file paths: "src/foo.py" matches that specific file
    - Glob patterns: "src/**/*.py", "*.ts" via pathlib (** aware)
    """
    for pattern in filter_paths:
        # Glob pattern — delegate to _matches_glob (handles ** correctly)
        if any(ch in pattern for ch in "*?[]"):
            if _matches_glob(rel_path, pattern):
                return True
            continue
        # Normalize potential directory patterns like "src/" -> "src"
        normalized = pattern.rstrip("/")
        # Exact match
        if rel_path in (pattern, normalized):
            return True
        # Directory prefix — require path boundary to avoid "src" matching "src2/"
        if normalized and rel_path.startswith(normalized + "/"):
            return True
    return False
