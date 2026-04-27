"""Language detection, markers, globs, grammar and validation utilities."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from coderecon.core.languages import (
    ALL_LANGUAGES,
    Language,
    _get_language_family,
)

if TYPE_CHECKING:
    from coderecon.index.models import LanguageFamily

# Lookup Tables (built from ALL_LANGUAGES)

LANGUAGES_BY_NAME: dict[str, Language] = {lang.name: lang for lang in ALL_LANGUAGES}

_DEFAULT_EXPORTABLE_KINDS: frozenset[str] = frozenset({"function", "class"})

def exportable_kinds_for_language(language_name: str) -> frozenset[str]:
    """Return the set of def kinds that are top-level exportable for a language."""
    lang = LANGUAGES_BY_NAME.get(language_name)
    if lang is not None:
        return lang.exportable_kinds
    return _DEFAULT_EXPORTABLE_KINDS

def is_name_exported(name: str, language_name: str) -> bool:
    """Check if a symbol name is exported according to language conventions.
    Per-language rules:
    - Python: names starting with ``_`` are private
    - Go: names starting with a lowercase letter are unexported
    - All other languages: names are exported by default (visibility is
      controlled by language keywords that we don't track in DefFact)
    """
    if language_name == "python":
        return not name.startswith("_")
    if language_name == "go":
        return len(name) > 0 and name[0].isupper()
    # Default: assume exported (most languages use keyword-based visibility)
    return True

# ── Cross-language kind taxonomies ───────────────────────────────
#
# These are UNIVERSAL sets covering all languages.  Extra entries that
# don't appear in a particular language's def table are harmless —
# they simply never match.  Keeping them here satisfies the rule that
# *all* language knowledge lives in this module.

TYPE_KINDS: frozenset[str] = frozenset({
    "class", "struct", "interface", "trait", "enum", "record",
    "protocol", "object", "data", "newtype", "type_class",
    "abstract_type", "type",
})
"""Def kinds that represent type declarations (participate in type
hierarchy, receiver shape matching, co-implementor edges)."""

CONTAINER_KINDS: frozenset[str] = frozenset({
    "class", "struct", "enum", "interface", "trait",
    "module", "namespace", "impl", "protocol", "object",
    "record", "type_class",
})
"""Def kinds that can contain nested symbol definitions (used for
hierarchical symbol tree display)."""

CONSTANT_KINDS: frozenset[str] = frozenset({
    "variable", "constant", "val", "var", "property", "field",
})
"""Def kinds that are low-signal for display purposes (constants,
variables, fields).  Used as an exclusion filter in scaffolds."""

def _build_extension_multimap() -> dict[str, tuple[str, ...]]:
    """Build extension -> families mapping, sorted by priority (highest first).
    Extensions that appear in multiple families (like .m, .v, .h) will have
    all families listed, with highest-priority first.
    """
    ext_to_families: dict[str, list[tuple[int, str]]] = {}
    for lang in ALL_LANGUAGES:
        for ext in lang.extensions:
            key = ext.lower()
            if key not in ext_to_families:
                ext_to_families[key] = []
            ext_to_families[key].append((lang.priority, lang.name))
    # Sort by priority descending, return just families
    result: dict[str, tuple[str, ...]] = {}
    for ext, families in ext_to_families.items():
        families.sort(key=lambda x: -x[0])  # Higher priority first
        result[ext] = tuple(f for _, f in families)
    return result

def _build_filename_map() -> dict[str, str]:
    """Build lowercase filename -> primary name mapping."""
    result: dict[str, str] = {}
    # Process in priority order
    sorted_langs = sorted(ALL_LANGUAGES, key=lambda lang: -lang.priority)
    for lang in sorted_langs:
        for filename in lang.filenames:
            key = filename.lower()
            if key not in result:
                result[key] = lang.name
    return result

# Extension -> all names (highest priority first)
EXTENSION_TO_NAMES: dict[str, tuple[str, ...]] = _build_extension_multimap()

# Extension -> primary name (highest priority)
EXTENSION_TO_NAME: dict[str, str] = {
    ext: names[0] for ext, names in EXTENSION_TO_NAMES.items() if names
}

# Filename -> name
FILENAME_TO_NAME: dict[str, str] = _build_filename_map()

# Ambient names (indexed without project markers)
AMBIENT_NAMES: frozenset[str] = frozenset(lang.name for lang in ALL_LANGUAGES if lang.ambient)

# Compound suffixes that need special handling (longer compounds checked first)
_COMPOUND_SUFFIXES: dict[str, str] = {
    ".d.ts.map": "javascript",  # Source maps for TypeScript declarations
    ".d.ts": "javascript",  # TypeScript declaration files
    ".spec.ts": "javascript",  # Keep as JS name (test file)
    ".test.ts": "javascript",  # Keep as JS name (test file)
}

# Ambiguous Extension Documentation
# These extensions map to multiple families. Use context (project markers,
# directory structure) to disambiguate when possible.

AMBIGUOUS_EXTENSIONS: dict[str, tuple[str, ...]] = {
    ext: families for ext, families in EXTENSION_TO_NAMES.items() if len(families) > 1
}



def get_families_for_extension(ext: str) -> tuple[str, ...]:
    """Get all language families that use this extension, ordered by priority.
    For ambiguous extensions like .m (objc, matlab) or .v (verilog, vlang),
    returns all matching families. Caller should use context to pick the right one.
    Args:
        ext: File extension including dot (e.g., ".py", ".m")
    Returns:
        Tuple of name names, highest priority first. Empty if unknown.
    """
    return EXTENSION_TO_NAMES.get(ext.lower(), ())

def is_ambiguous_extension(ext: str) -> bool:
    """Check if an extension maps to multiple language families."""
    return len(get_families_for_extension(ext)) > 1

def detect_language_family(path: str | Path) -> str | None:
    """Detect the primary language name for a file path.
    Detection order:
    1. Exact filename match (e.g., "Makefile", "Dockerfile")
    2. Compound suffix match (e.g., ".d.ts")
    3. Simple suffix match (e.g., ".py")
    For ambiguous extensions (e.g., .v, .m), returns the highest-priority name.
    Use get_families_for_extension() if you need all candidates, or use
    context-aware detection in the scanner layer.
    Args:
        path: File path (string or Path)
    Returns:
        Family name or None if unknown.
    """
    path_str = str(path) if isinstance(path, Path) else path
    basename = os.path.basename(path_str).lower()
    # 1. Exact filename match
    if name := FILENAME_TO_NAME.get(basename):
        return name
    # 2. Compound suffix check (only when multiple dots in basename)
    if basename.count(".") >= 2:
        for compound, lang in _COMPOUND_SUFFIXES.items():
            if basename.endswith(compound):
                return lang
    # 3. Simple suffix match (returns highest priority name)
    _, ext = os.path.splitext(path_str)
    return EXTENSION_TO_NAME.get(ext.lower()) if ext else None

def detect_language_family_enum(path: str | Path) -> LanguageFamily | None:
    """Detect language name and return as LanguageFamily enum.
    Returns None if:
    - File type is unknown
    - Family string doesn't match any LanguageFamily value
    """
    if (name := detect_language_family(path)) is None:
        return None
    try:
        result: LanguageFamily = _get_language_family()(name)
        return result
    except ValueError:
        return None

# Include Glob Generation

def _generate_include_globs(lang: Language) -> tuple[str, ...]:
    """Generate include globs from BOTH extensions AND filenames.
    This ensures all declared extensions and filenames are covered by globs.
    """
    globs: list[str] = []
    # Add extension-based globs
    for ext in sorted(lang.extensions):
        globs.append(f"**/*{ext}")
    # Add filename-based globs (exact filename matches at any depth)
    for name in sorted(lang.filenames):
        globs.append(f"**/{name}")
        # Also match common variants (e.g., Dockerfile.dev, Dockerfile.prod)
        if name in ("dockerfile",):
            globs.append(f"**/{name}.*")
    return tuple(globs)

def get_include_globs(name: str) -> tuple[str, ...]:
    """Get include globs for a language name.
    Globs cover both extensions and special filenames.
    """
    if name not in LANGUAGES_BY_NAME:
        return ()
    return _generate_include_globs(LANGUAGES_BY_NAME[name])

# Marker Functions

def get_markers(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Get (workspace_markers, package_markers) for a name.
    All markers are EXACT filenames (lowercase).
    """
    if (lang := LANGUAGES_BY_NAME.get(name)) is None:
        return (), ()
    return lang.markers_workspace, lang.markers_package

def build_marker_definitions() -> dict[str, dict[str, tuple[str, ...]]]:
    """Build {name: {"workspace": (...), "package": (...)}} for scanner.
    All marker strings are exact lowercase filenames.
    """
    result: dict[str, dict[str, tuple[str, ...]]] = {}
    for lang in ALL_LANGUAGES:
        if lang.markers_workspace or lang.markers_package:
            result[lang.name] = {
                "workspace": lang.markers_workspace,
                "package": lang.markers_package,
            }
    return result

def build_include_specs() -> dict[str, tuple[str, ...]]:
    """Build {name: globs} for scanner.
    Globs cover both extensions and filenames.
    """
    result: dict[str, tuple[str, ...]] = {}
    for lang in ALL_LANGUAGES:
        globs = _generate_include_globs(lang)
        if globs:
            result[lang.name] = globs
    return result

# Other Accessors


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

# Validation (for tests only - NOT run at import time)

def validate_language_families() -> list[str]:
    """Validate that all name strings match LanguageFamily enum values.
    Returns list of error messages (empty if valid).
    Call this in a unit test to catch definition/enum mismatches.
    """
    errors: list[str] = []
    try:
        LanguageFamily = _get_language_family()
        enum_values = {e.value for e in LanguageFamily}
        defined_families = set(LANGUAGES_BY_NAME.keys())
        missing_in_enum = defined_families - enum_values
        if missing_in_enum:
            errors.append(
                f"Families defined in languages.py but missing from LanguageFamily enum: "
                f"{sorted(missing_in_enum)}"
            )
    except (ImportError, AttributeError) as e:
        errors.append(f"Could not import LanguageFamily: {e}")
    return errors

def validate_markers_are_exact_filenames() -> list[str]:
    """Validate that all markers are exact filenames (no wildcards/globs).
    Returns list of error messages (empty if valid).
    """
    errors: list[str] = []
    for lang in ALL_LANGUAGES:
        for marker in lang.markers_workspace + lang.markers_package:
            if "*" in marker or "?" in marker:
                errors.append(
                    f"{lang.name}: marker '{marker}' contains wildcards. "
                    f"Markers must be exact filenames."
                )
            if marker.startswith(".") and marker.count(".") == 1 and len(marker) > 1:
                # Looks like an extension (e.g., .csproj)
                errors.append(
                    f"{lang.name}: marker '{marker}' looks like an extension, not a filename. "
                    f"Markers must be exact filenames."
                )
    return errors
