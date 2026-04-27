"""Refactor operations - refactor_* tools implementation.

Index-based refactoring with probabilistic candidate sets.
Per SPEC.md §23.7 refactor tool specification.

Uses DefFact/RefFact from the index to find candidate rename sites.
Candidates are ranked by certainty - agent reviews before applying.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import structlog

if TYPE_CHECKING:
    from coderecon.index.models import RefFact
    from coderecon.index.ops import IndexCoordinatorEngine
    from coderecon.mutation.ops import Edit, MutationDelta, MutationOps

log = structlog.get_logger(__name__)

RefactorAction = Literal["rename", "move", "delete", "preview", "apply", "cancel"]

@dataclass
class EditHunk:
    """A single edit hunk in a refactor preview."""
    old: str
    new: str
    line: int
    certainty: Literal["high", "medium", "low"]
@dataclass
class FileEdit:
    """Edits for a single file in refactor preview."""
    path: str
    hunks: list[EditHunk] = field(default_factory=list)
@dataclass
class RefactorPreview:
    """Preview of refactoring changes."""
    files_affected: int
    edits: list[FileEdit] = field(default_factory=list)
    contexts_used: list[str] = field(default_factory=list)
    high_certainty_count: int = 0
    medium_certainty_count: int = 0
    low_certainty_count: int = 0
    # Verification guidance for agents
    verification_required: bool = False
    low_certainty_files: list[str] = field(default_factory=list)
    verification_guidance: str | None = None
    # File move metadata (set by move(), consumed by apply())
    move_from: str | None = None
    move_to: str | None = None
@dataclass
class InspectResult:
    """Result of inspecting low-certainty matches in a file."""
    path: str
    matches: list[dict[str, str | int]]  # {line, snippet, context_before, context_after}
@dataclass
class RefactorDivergence:
    """Divergence detected during refactoring."""
    conflicting_hunks: list[dict[str, str | list[str]]] = field(default_factory=list)
    resolution_options: list[str] = field(default_factory=list)
@dataclass
class RefactorResult:
    """Result of refactor operation."""
    refactor_id: str
    status: Literal["previewed", "applied", "cancelled", "divergence"]
    preview: RefactorPreview | None = None
    applied: MutationDelta | None = None
    changed_paths: list[Path] = field(default_factory=list)
    divergence: RefactorDivergence | None = None
    warning: str | None = None  # Agent guidance (e.g., path:line:col format detected)
def _scan_file_for_comment_occurrences(
    content: str,
    symbol: str,
    language: str | None,
) -> list[tuple[int, str]]:
    """Scan file content for symbol occurrences in comments and docstrings.
    Returns list of (line_number, context_snippet) tuples.
    """
    occurrences: list[tuple[int, str]] = []
    lines = content.splitlines()
    # Patterns for comments and docstrings by language
    if language in ("python", None):
        # Python: # comments, triple-quoted strings
        in_docstring = False
        docstring_delimiter = None
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Check for docstring boundaries
            if not in_docstring and (stripped.startswith('"""') or stripped.startswith("'''")):
                docstring_delimiter = stripped[:3]
                in_docstring = True
                # Check if ends on same line
                if stripped.count(docstring_delimiter) >= 2:
                    in_docstring = False
                    if _word_boundary_match(line, symbol):
                        occurrences.append((i, stripped[:60]))
                elif _word_boundary_match(line, symbol):
                    occurrences.append((i, stripped[:60]))
                continue
            if in_docstring:
                if docstring_delimiter and docstring_delimiter in stripped[3:]:
                    in_docstring = False
                if _word_boundary_match(line, symbol):
                    occurrences.append((i, stripped[:60]))
                continue
            # Check for # comments
            if "#" in line:
                comment_start = line.index("#")
                comment_text = line[comment_start:]
                if _word_boundary_match(comment_text, symbol):
                    occurrences.append((i, stripped[:60]))
    elif language in ("javascript", "typescript", "java", "go", "rust", "cpp"):
        # C-style: // comments, /* */ blocks, and JSDoc /** */
        in_block_comment = False
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if in_block_comment:
                if "*/" in line:
                    in_block_comment = False
                if _word_boundary_match(line, symbol):
                    occurrences.append((i, stripped[:60]))
                continue
            if "/*" in line:
                in_block_comment = True
                if "*/" in line[line.index("/*") + 2 :]:
                    in_block_comment = False
                if _word_boundary_match(line, symbol):
                    occurrences.append((i, stripped[:60]))
                continue
            # Check for // comments
            if "//" in line:
                comment_start = line.index("//")
                comment_text = line[comment_start:]
                if _word_boundary_match(comment_text, symbol):
                    occurrences.append((i, stripped[:60]))
    return occurrences

def _word_boundary_match(text: str, symbol: str) -> bool:
    """Check if symbol appears in text as a whole word."""
    pattern = rf"\b{re.escape(symbol)}\b"
    return bool(re.search(pattern, text))

def _compute_rename_certainty_from_ref(ref: RefFact) -> Literal["high", "medium", "low"]:
    """
    Compute certainty for a rename candidate based on RefFact properties.
    Certainty tiers (per SPEC.md §7.3):
    - PROVEN refs: Same-file lexical bind with LocalBindFact certainty=CERTAIN -> "high"
    - STRONG refs: Cross-file with explicit ImportFact + ExportSurface trace -> "high"
    - ANCHORED refs: Ambiguous but grouped in AnchorGroup -> "medium"
    - UNKNOWN refs: Cannot classify -> "low"
    Also considers the RefFact's own certainty field as a fallback.
    """
    # Check ref_tier first (most authoritative)
    ref_tier = ref.ref_tier
    if ref_tier:
        if ref_tier in ("PROVEN", "proven"):
            return "high"
        elif ref_tier in ("STRONG", "strong"):
            return "high"  # Explicit import trace
        elif ref_tier in ("ANCHORED", "anchored"):
            return "medium"
        # UNKNOWN falls through to certainty check
    # Fallback to certainty field
    certainty = ref.certainty
    if certainty in ("CERTAIN", "certain"):
        return "high"
    return "low"

