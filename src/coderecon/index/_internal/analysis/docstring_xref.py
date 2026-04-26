"""Docstring cross-reference resolution.

Regex-parses structured references from docstrings/comments:
  - `See also FooClass`
  - `:func:`module.func``
  - `:class:`module.Class``
  - `[BarClass](...)` (markdown links)
  - `#symbol` (hash references)
  - bare `SymbolName` references in docstrings

Resolves each to a DefFact.def_uid and persists as DocCrossRef.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine


# Patterns for cross-references in docstrings/comments
_PATTERNS = [
    # Sphinx :role:`target` — :func:`foo.bar`, :class:`Baz`, :meth:`Cls.method`
    re.compile(r":(?:func|class|meth|attr|mod|data|const|exc|obj):`~?([^`]+)`"),
    # "See also" / "See" references — "See also FooClass", "See :func:`bar`"
    re.compile(r"[Ss]ee\s+(?:also\s+)?[`:]*([A-Z][A-Za-z0-9_.]+)"),
    # Markdown link [SymbolName](...) or [SymbolName][]
    re.compile(r"\[([A-Z][A-Za-z0-9_.]+)\]\("),
    re.compile(r"\[([A-Z][A-Za-z0-9_.]+)\]\[\]"),
    # Hash references: #SymbolName
    re.compile(r"(?:^|\s)#([A-Z][A-Za-z0-9_.]+)"),
    # Google/numpy style "Returns: ClassName" or "Raises: ExceptionName"
    re.compile(r"(?:Returns?|Raises?|Yields?|Args?):\s*([A-Z][A-Za-z0-9_.]+)"),
]

# Bare qualified name pattern (module.Class.method) — high confidence
_QUALIFIED_RE = re.compile(r"(?<!\w)([a-z_][a-z0-9_]*(?:\.[A-Z][A-Za-z0-9_]*)+)(?!\w)")

# Bare CamelCase in docstrings — medium confidence
_CAMELCASE_RE = re.compile(r"(?<![A-Za-z])([A-Z][a-z]+(?:[A-Z][a-z]+)+)(?![A-Za-z])")


@dataclass(frozen=True, slots=True)
class RawCrossRef:
    """A raw cross-reference extracted from text."""

    source_line: int
    raw_text: str
    target_name: str  # unresolved name
    confidence: str  # high, medium, low


def extract_cross_refs(
    text_content: str,
    start_line: int = 1,
) -> list[RawCrossRef]:
    """Extract cross-references from docstring/comment text.

    Args:
        text_content: The docstring or comment text.
        start_line: Line offset for the text within the file.

    Returns:
        List of raw cross-references with line numbers.
    """
    refs: list[RawCrossRef] = []
    seen: set[tuple[int, str]] = set()

    for line_offset, line in enumerate(text_content.splitlines()):
        abs_line = start_line + line_offset

        # High confidence: Sphinx roles, "See also", markdown links
        for pattern in _PATTERNS:
            for match in pattern.finditer(line):
                target = match.group(1).strip()
                if not target or len(target) < 2:  # noqa: PLR2004
                    continue
                key = (abs_line, target)
                if key not in seen:
                    seen.add(key)
                    refs.append(RawCrossRef(
                        source_line=abs_line,
                        raw_text=match.group(0).strip(),
                        target_name=target,
                        confidence="high",
                    ))

        # Medium confidence: qualified names
        for match in _QUALIFIED_RE.finditer(line):
            target = match.group(1)
            key = (abs_line, target)
            if key not in seen:
                seen.add(key)
                refs.append(RawCrossRef(
                    source_line=abs_line,
                    raw_text=target,
                    target_name=target,
                    confidence="medium",
                ))

        # Low confidence: CamelCase (only in lines that look like docstrings)
        stripped = line.strip()
        if stripped and not stripped.startswith(("def ", "class ", "import ", "from ")):
            for match in _CAMELCASE_RE.finditer(line):
                target = match.group(1)
                key = (abs_line, target)
                if key not in seen:
                    seen.add(key)
                    refs.append(RawCrossRef(
                        source_line=abs_line,
                        raw_text=target,
                        target_name=target,
                        confidence="low",
                    ))

    return refs


def resolve_and_persist(
    engine: Engine,
    file_id: int,
    source_def_uid: str | None,
    raw_refs: list[RawCrossRef],
) -> int:
    """Resolve raw cross-refs against DefFact and persist as DocCrossRef.

    Attempts to match target_name against:
    1. Exact def_uid match
    2. Name-only match (unqualified)
    3. Suffix match (qualified tail)

    Returns number of cross-refs persisted.
    """
    if not raw_refs:
        return 0

    written = 0

    with engine.connect() as conn:
        for ref in raw_refs:
            target_uid = _resolve_target(conn, ref.target_name)
            if target_uid is None:
                continue

            # Upsert: avoid duplicates
            existing = conn.execute(
                text(
                    "SELECT id FROM doc_cross_refs "
                    "WHERE source_file_id = :fid AND source_line = :line "
                    "AND target_def_uid = :target"
                ),
                {"fid": file_id, "line": ref.source_line, "target": target_uid},
            ).fetchone()

            if existing:
                continue

            conn.execute(
                text(
                    "INSERT INTO doc_cross_refs "
                    "(source_file_id, source_def_uid, source_line, raw_text, "
                    "target_def_uid, confidence) "
                    "VALUES (:fid, :suid, :line, :raw, :target, :conf)"
                ),
                {
                    "fid": file_id,
                    "suid": source_def_uid,
                    "line": ref.source_line,
                    "raw": ref.raw_text,
                    "target": target_uid,
                    "conf": ref.confidence,
                },
            )
            written += 1

        conn.commit()

    return written


def _resolve_target(conn: Connection, target_name: str) -> str | None:
    """Resolve a target name to a def_uid.

    Tries in order:
    1. Exact def_uid match
    2. Name-only match
    3. Qualified suffix match
    """
    # 1. Exact def_uid
    row = conn.execute(
        text("SELECT def_uid FROM def_facts WHERE def_uid = :name LIMIT 1"),
        {"name": target_name},
    ).fetchone()
    if row:
        return row[0]

    # 2. Name-only match (last segment)
    simple_name = target_name.rsplit(".", 1)[-1]
    row = conn.execute(
        text("SELECT def_uid FROM def_facts WHERE name = :name LIMIT 1"),
        {"name": simple_name},
    ).fetchone()
    if row:
        return row[0]

    # 3. Suffix match on def_uid (e.g., "module.Class" matches "pkg.module.Class")
    row = conn.execute(
        text("SELECT def_uid FROM def_facts WHERE def_uid LIKE :suffix LIMIT 1"),
        {"suffix": f"%.{target_name}"},
    ).fetchone()
    if row:
        return row[0]

    return None
