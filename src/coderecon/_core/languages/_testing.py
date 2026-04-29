"""Test file detection and convention-based test-pairing functions."""

from __future__ import annotations

import os
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from coderecon._core.languages import (
    ALL_LANGUAGES,
    LANGUAGES_BY_NAME,
)
from coderecon._core.languages._util import detect_language_family

def get_test_patterns(name: str) -> tuple[str, ...]:
    """Get test file patterns for a name."""
    return LANGUAGES_BY_NAME[name].test_patterns if name in LANGUAGES_BY_NAME else ()

def is_test_file(path: str | Path) -> bool:
    """Check if a file path matches any known test file pattern.
    Uses the canonical ``test_patterns`` defined on each ``Language`` in
    ``ALL_LANGUAGES``.  Patterns are ``fnmatch``-style globs matched
    against the filename (e.g. ``test_*.py``, ``*_test.go``).
    Additionally, if a pattern contains ``/`` it is matched against the
    full path string via ``fnmatch``.  Note that ``fnmatch`` requires the
    pattern to match the *entire* string, so a bare prefix like ``tests/``
    will **not** match; use ``tests/*`` or ``tests/*.py`` instead.
    Args:
        path: File path (string or Path object).
    Returns:
        True if the file matches any test pattern for any language.
    """
    p = Path(path) if isinstance(path, str) else path
    name = p.name
    path_str = str(p)
    # Normalize to POSIX-style separators so directory-style patterns
    # like "spec/**/*.cr" reliably match on Windows as well.
    path_str_posix = path_str.replace(os.sep, "/")
    for lang in ALL_LANGUAGES:
        for pattern in lang.test_patterns:
            if "/" in pattern:
                # Directory-style pattern — use fnmatch against normalized full path
                if fnmatch(path_str_posix, pattern):
                    return True
            else:
                if fnmatch(name, pattern):
                    return True
    return False

# Convention-based test file pairing

# Common source→test directory mappings.  Order matters: first match wins.
_SRC_TEST_DIR_PAIRS: tuple[tuple[str, str], ...] = (
    ("src/", "tests/"),
    ("src/", "test/"),
    ("lib/", "tests/"),
    ("lib/", "test/"),
    ("lib/", "spec/"),
    ("app/", "tests/"),
    ("app/", "test/"),
    ("app/", "spec/"),
)

def find_test_pairs(source_path: str) -> list[str]:
    """Return plausible test file paths for a source file.
    Uses language-specific naming conventions to derive candidate test
    paths.  The function is *pure* — it does NOT check the filesystem.
    Callers should filter the results to paths that actually exist.
    Convention rules (applied per language):
    Python (``test_*.py`` / ``*_test.py``)::
        src/coderecon/foo/bar.py  →  tests/foo/test_bar.py
                                     tests/foo/bar_test.py
    JavaScript / TypeScript (``*.test.{js,ts,tsx}`` / ``*.spec.{…}``)::
        src/components/Button.tsx →  src/components/Button.test.tsx
                                     src/components/__tests__/Button.tsx
                                     tests/components/Button.test.tsx
    Go (``*_test.go`` — same directory by convention)::
        pkg/server/handler.go    →  pkg/server/handler_test.go
    Ruby (``*_spec.rb`` / ``*_test.rb``)::
        lib/models/user.rb       →  spec/models/user_spec.rb
                                     test/models/user_test.rb
    Args:
        source_path: Relative POSIX-style source file path.
    Returns:
        List of candidate test paths (may be empty if language unknown
        or the file is already a test file).  Paths are relative,
        POSIX-style.
    """
    if is_test_file(source_path):
        return []
    lang = detect_language_family(source_path)
    if not lang:
        return []
    p = PurePosixPath(source_path)
    stem = p.stem  # "bar" from "bar.py"
    ext = p.suffix  # ".py"
    parent = str(p.parent)  # "src/coderecon/foo"
    candidates: list[str] = []
    if lang == "python":
        _add_python_test_pairs(candidates, parent, stem, ext)
    elif lang in ("javascript", "typescript"):
        _add_js_ts_test_pairs(candidates, parent, stem, ext)
    elif lang == "go":
        _add_go_test_pairs(candidates, parent, stem, ext)
    elif lang == "ruby":
        _add_ruby_test_pairs(candidates, parent, stem, ext)
    elif lang == "rust":
        _add_rust_test_pairs(candidates, parent, stem, ext)
    elif lang == "java":
        _add_java_test_pairs(candidates, parent, stem, ext)
    elif lang in ("csharp", "fsharp"):
        _add_dotnet_test_pairs(candidates, parent, stem, ext)
    elif lang == "php":
        _add_php_test_pairs(candidates, parent, stem, ext)
    elif lang == "elixir":
        _add_elixir_test_pairs(candidates, parent, stem)
    else:
        # Generic fallback: test_<stem><ext> and <stem>_test<ext>
        _add_generic_test_pairs(candidates, parent, stem, ext)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique

def _swap_src_dir(parent: str, test_dir_name: str) -> str | None:
    """Swap a source directory prefix with a test directory prefix.
    ``src/coderecon/foo`` → ``tests/foo`` (strips the first component
    after ``src/``).  Returns *None* if the parent doesn't start with
    a known source directory.
    """
    for src_prefix, _test_prefix in _SRC_TEST_DIR_PAIRS:
        if parent.startswith(src_prefix):
            rest = parent[len(src_prefix) :]
            # For Python: src/coderecon/foo → tests/foo (strip package root)
            parts = rest.split("/", 1)
            if len(parts) > 1:
                return test_dir_name + parts[1]
            return test_dir_name + rest
    return None

def _add_python_test_pairs(
    out: list[str],
    parent: str,
    stem: str,
    ext: str,
) -> None:
    """Python: test_<stem>.py alongside and in tests/ mirror."""
    # Same directory
    out.append(f"{parent}/test_{stem}{ext}")
    out.append(f"{parent}/{stem}_test{ext}")
    # Mirror into tests/ directory
    swapped = _swap_src_dir(parent, "tests/")
    if swapped:
        out.append(f"{swapped}/test_{stem}{ext}")
        out.append(f"{swapped}/{stem}_test{ext}")

def _add_js_ts_test_pairs(
    out: list[str],
    parent: str,
    stem: str,
    ext: str,
) -> None:
    """JS/TS: <stem>.test.{ext}, <stem>.spec.{ext}, __tests__/<stem>.{ext}."""
    # Co-located
    out.append(f"{parent}/{stem}.test{ext}")
    out.append(f"{parent}/{stem}.spec{ext}")
    # __tests__ subdirectory
    out.append(f"{parent}/__tests__/{stem}{ext}")
    out.append(f"{parent}/__tests__/{stem}.test{ext}")
    # Mirror into tests/
    swapped = _swap_src_dir(parent, "tests/")
    if swapped:
        out.append(f"{swapped}/{stem}.test{ext}")
        out.append(f"{swapped}/{stem}.spec{ext}")

def _add_go_test_pairs(
    out: list[str],
    parent: str,
    stem: str,
    ext: str,
) -> None:
    """Go: <stem>_test.go in the same directory."""
    out.append(f"{parent}/{stem}_test{ext}")

def _add_ruby_test_pairs(
    out: list[str],
    parent: str,
    stem: str,
    ext: str,
) -> None:
    """Ruby: <stem>_spec.rb in spec/, <stem>_test.rb in test/."""
    out.append(f"{parent}/{stem}_spec{ext}")
    out.append(f"{parent}/{stem}_test{ext}")
    for src_prefix, _ in _SRC_TEST_DIR_PAIRS:
        if parent.startswith(src_prefix):
            rest = parent[len(src_prefix) :]
            # Use spec/ and test/ mirrors (Ruby convention)
            out.append(f"spec/{rest}/{stem}_spec{ext}")
            out.append(f"test/{rest}/{stem}_test{ext}")
            break

def _add_rust_test_pairs(
    out: list[str],
    parent: str,
    stem: str,
    ext: str,
) -> None:
    """Rust: test_<stem>.rs, <stem>_test.rs, and tests/<stem>.rs."""
    out.append(f"{parent}/test_{stem}{ext}")
    out.append(f"{parent}/{stem}_test{ext}")
    # Rust convention: tests/ at crate root
    # If parent is src/ or src/<subdir>, add tests/<stem>.rs
    if parent.startswith("src"):
        out.append(f"tests/{stem}{ext}")

def _add_java_test_pairs(
    out: list[str],
    parent: str,
    stem: str,
    ext: str,
) -> None:
    """Java: <Stem>Test.java, mirrored from src/main to src/test."""
    out.append(f"{parent}/{stem}Test{ext}")
    # Maven/Gradle convention: src/main/java/… → src/test/java/…
    if "src/main/" in parent:
        test_parent = parent.replace("src/main/", "src/test/", 1)
        out.append(f"{test_parent}/{stem}Test{ext}")

def _add_dotnet_test_pairs(
    out: list[str],
    parent: str,
    stem: str,
    ext: str,
) -> None:
    """.NET: <Stem>Tests.{cs,fs}, <Stem>Test.{cs,fs}."""
    out.append(f"{parent}/{stem}Tests{ext}")
    out.append(f"{parent}/{stem}Test{ext}")

def _add_php_test_pairs(
    out: list[str],
    parent: str,
    stem: str,
    ext: str,
) -> None:
    """PHP: <Stem>Test.php."""
    out.append(f"{parent}/{stem}Test{ext}")
    swapped = _swap_src_dir(parent, "tests/")
    if swapped:
        out.append(f"{swapped}/{stem}Test{ext}")

def _add_elixir_test_pairs(
    out: list[str],
    parent: str,
    stem: str,
) -> None:
    """Elixir: <stem>_test.exs in test/ mirror."""
    out.append(f"{parent}/{stem}_test.exs")
    if parent.startswith("lib/"):
        test_parent = "test/" + parent[len("lib/") :]
        out.append(f"{test_parent}/{stem}_test.exs")

def _add_generic_test_pairs(
    out: list[str],
    parent: str,
    stem: str,
    ext: str,
) -> None:
    """Generic fallback: test_<stem> and <stem>_test."""
    out.append(f"{parent}/test_{stem}{ext}")
    out.append(f"{parent}/{stem}_test{ext}")
    swapped = _swap_src_dir(parent, "tests/")
    if swapped:
        out.append(f"{swapped}/test_{stem}{ext}")
        out.append(f"{swapped}/{stem}_test{ext}")

def get_grammar_name(name: str) -> str | None:
    """Get tree-sitter grammar name for a name.
    Returns None if no tree-sitter grammar is available for the language.
    """
    return LANGUAGES_BY_NAME[name].grammar if name in LANGUAGES_BY_NAME else None

def has_grammar(name: str) -> bool:
    """Check if name has a usable tree-sitter grammar."""
    return get_grammar_name(name) is not None

def get_all_indexable_extensions() -> set[str]:
    """Get all known file extensions."""
    return set(EXTENSION_TO_NAME.keys())

def get_all_indexable_filenames() -> set[str]:
    """Get all known special filenames."""
    return set(FILENAME_TO_NAME.keys())
