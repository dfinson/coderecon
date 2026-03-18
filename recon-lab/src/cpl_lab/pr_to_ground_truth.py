"""Convert PR data to ground truth JSON matching the existing schema.

Parses unified diffs, maps changed hunks to def_facts in the coderecon
index, infers tier assignments (minimum_sufficient vs thrash_preventing),
and generates query variants from issue text.

Output matches the schema expected by ``collector.py`` and validated by
``validate_ground_truth.py``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Diff parsing ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Hunk:
    """A changed region in a file."""

    start_line: int
    line_count: int

    @property
    def end_line(self) -> int:
        return self.start_line + max(self.line_count - 1, 0)


@dataclass(frozen=True)
class FileDiff:
    """Parsed diff for a single file."""

    path: str
    hunks: tuple[Hunk, ...]
    is_new_file: bool = False
    is_deleted: bool = False


_DIFF_HEADER = re.compile(r"^diff --git a/.+ b/(.+)$")
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified diff into per-file hunk lists.

    Extracts the **new-side** (post-change) line ranges from ``@@`` headers.
    These ranges are what we match against the current index.
    """
    files: list[FileDiff] = []
    current_path: str | None = None
    current_hunks: list[Hunk] = []
    is_new = is_deleted = False

    for line in diff_text.split("\n"):
        header_match = _DIFF_HEADER.match(line)
        if header_match:
            # Flush previous file
            if current_path is not None:
                files.append(FileDiff(
                    path=current_path,
                    hunks=tuple(current_hunks),
                    is_new_file=is_new,
                    is_deleted=is_deleted,
                ))
            current_path = header_match.group(1)
            current_hunks = []
            is_new = is_deleted = False
            continue

        if line.startswith("new file mode"):
            is_new = True
        elif line.startswith("deleted file mode"):
            is_deleted = True

        hunk_match = _HUNK_HEADER.match(line)
        if hunk_match and current_path is not None:
            start = int(hunk_match.group(1))
            count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            if count > 0:  # skip empty hunks
                current_hunks.append(Hunk(start_line=start, line_count=count))

    # Flush last file
    if current_path is not None:
        files.append(FileDiff(
            path=current_path,
            hunks=tuple(current_hunks),
            is_new_file=is_new,
            is_deleted=is_deleted,
        ))

    return files


# ── Identifier extraction from diffs ─────────────────────────────

_IDENT_PATTERN = re.compile(r"\b([A-Za-z_]\w{2,})\b")
_NOISE_WORDS = frozenset({
    "def", "class", "self", "return", "import", "from", "None", "True",
    "False", "and", "not", "the", "for", "with", "this", "that", "new",
    "public", "private", "protected", "static", "void", "int", "str",
    "string", "bool", "float", "double", "var", "let", "const", "func",
    "function", "async", "await", "else", "elif", "except", "try",
    "catch", "throw", "throws", "override", "virtual", "abstract",
})


def extract_identifiers_from_diff(diff_text: str) -> list[str]:
    """Extract meaningful identifiers from added/changed lines in a diff."""
    idents: dict[str, int] = {}
    for line in diff_text.split("\n"):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for m in _IDENT_PATTERN.finditer(line[1:]):
            word = m.group(1)
            if word not in _NOISE_WORDS and not word.isupper():
                idents[word] = idents.get(word, 0) + 1

    # Return by frequency, most common first
    return [w for w, _ in sorted(idents.items(), key=lambda x: -x[1])]


# ── Def mapping against index ────────────────────────────────────


@dataclass
class DefEntry:
    """A definition from the coderecon index."""

    path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    reason: str = ""


def map_hunks_to_defs(
    file_diffs: list[FileDiff],
    index_db: Path,
) -> tuple[list[DefEntry], list[DefEntry], list[DefEntry]]:
    """Map changed hunks to definitions in the index.

    Returns:
        (minimum_sufficient, thrash_preventing, excluded)

        - minimum_sufficient: defs overlapping changed hunks
        - thrash_preventing: same-file defs NOT overlapping hunks + test file defs
        - excluded: defs in changed files but explicitly outside scope
    """
    con = sqlite3.connect(str(index_db))
    cur = con.cursor()

    min_suff: list[DefEntry] = []
    thrash_prev: list[DefEntry] = []
    excluded: list[DefEntry] = []

    changed_paths = {fd.path for fd in file_diffs}

    for fd in file_diffs:
        if fd.is_deleted:
            continue

        # Get all defs in this file
        all_defs_in_file = cur.execute(
            """
            SELECT d.name, d.kind, d.start_line, d.end_line
            FROM def_facts d
            JOIN files f ON d.file_id = f.id
            WHERE f.path = ?
            ORDER BY d.start_line
            """,
            (fd.path,),
        ).fetchall()

        if not all_defs_in_file:
            continue

        # Determine which defs overlap with changed hunks
        changed_def_keys: set[tuple[str, str, int]] = set()

        for hunk in fd.hunks:
            for name, kind, start, end in all_defs_in_file:
                if start <= hunk.end_line and end >= hunk.start_line:
                    key = (name, kind, start)
                    if key not in changed_def_keys:
                        changed_def_keys.add(key)
                        min_suff.append(DefEntry(
                            path=fd.path,
                            name=name,
                            kind=kind,
                            start_line=start,
                            end_line=end,
                            reason=f"Definition overlaps with changed hunk "
                                   f"(lines {hunk.start_line}-{hunk.end_line})",
                        ))

        # Remaining defs in the same file → thrash_preventing
        for name, kind, start, end in all_defs_in_file:
            key = (name, kind, start)
            if key not in changed_def_keys:
                thrash_prev.append(DefEntry(
                    path=fd.path,
                    name=name,
                    kind=kind,
                    start_line=start,
                    end_line=end,
                    reason="Same-file context def (not in changed hunk)",
                ))

    # Find test file defs for changed source files
    _add_test_file_defs(cur, changed_paths, thrash_prev)

    con.close()
    return min_suff, thrash_prev, excluded


def _add_test_file_defs(
    cur: sqlite3.Cursor,
    changed_paths: set[str],
    thrash_prev: list[DefEntry],
) -> None:
    """Find test file defs corresponding to changed source files."""
    existing_paths = {d.path for d in thrash_prev}

    for src_path in changed_paths:
        test_candidates = _guess_test_paths(src_path)
        for test_path in test_candidates:
            if test_path in existing_paths:
                continue
            rows = cur.execute(
                """
                SELECT d.name, d.kind, d.start_line, d.end_line, f.path
                FROM def_facts d
                JOIN files f ON d.file_id = f.id
                WHERE f.path = ?
                ORDER BY d.start_line
                """,
                (test_path,),
            ).fetchall()

            for name, kind, start, end, path in rows:
                thrash_prev.append(DefEntry(
                    path=path,
                    name=name,
                    kind=kind,
                    start_line=start,
                    end_line=end,
                    reason=f"Test file for changed source {src_path}",
                ))
            if rows:
                existing_paths.add(test_path)


def _guess_test_paths(src_path: str) -> list[str]:
    """Generate candidate test file paths for a source file.

    Covers common conventions across languages:
    - Python: src/foo.py → tests/test_foo.py, test/test_foo.py
    - JS/TS: src/foo.ts → src/foo.test.ts, __tests__/foo.test.ts
    - Java: src/main/.../Foo.java → src/test/.../FooTest.java
    - Go: foo.go → foo_test.go
    - Rust: src/foo.rs → tests/foo.rs
    - C#: Src/Foo.cs → Tests/FooTests.cs
    - PHP: src/Foo.php → tests/FooTest.php
    - Ruby: lib/foo.rb → spec/foo_spec.rb, test/test_foo.rb
    """
    candidates: list[str] = []
    parts = src_path.split("/")
    filename = parts[-1]
    stem, ext = (filename.rsplit(".", 1) + [""])[:2]

    if ext == "py":
        # tests/test_foo.py, test/test_foo.py
        for test_dir in ("tests", "test"):
            test_name = f"test_{stem}.{ext}"
            candidates.append("/".join(parts[:-1]).replace("src/", f"{test_dir}/", 1)
                              .replace(stem, "") + test_name
                              if "src/" in src_path
                              else f"{test_dir}/{test_name}")
        # Handle common Python layouts
        if parts[0] == "src" and len(parts) > 2:
            candidates.append(f"tests/{parts[2]}/test_{stem}.py")
        elif len(parts) >= 2:
            candidates.append(f"tests/test_{stem}.py")
            candidates.append(f"tests/{parts[0]}/test_{stem}.py")

    elif ext in ("ts", "tsx", "js", "jsx"):
        candidates.append(src_path.replace(f".{ext}", f".test.{ext}"))
        candidates.append(src_path.replace(f".{ext}", f".spec.{ext}"))
        base_dir = "/".join(parts[:-1])
        candidates.append(f"{base_dir}/__tests__/{stem}.test.{ext}")

    elif ext == "java":
        candidates.append(src_path.replace("/main/", "/test/")
                          .replace(f"{stem}.java", f"{stem}Test.java"))

    elif ext == "go":
        candidates.append(src_path.replace(f".{ext}", f"_test.{ext}"))

    elif ext == "rs":
        if "src/" in src_path:
            candidates.append(src_path.replace("src/", "tests/"))

    elif ext == "cs":
        for suffix in ("Tests", "Test"):
            candidates.append(src_path.replace("Src/", "Tests/")
                              .replace("src/", "tests/")
                              .replace(f"{stem}.cs", f"{stem}{suffix}.cs"))

    elif ext == "php":
        candidates.append(src_path.replace("src/", "tests/")
                          .replace(f"{stem}.php", f"{stem}Test.php"))

    elif ext == "rb":
        candidates.append(src_path.replace("lib/", "spec/")
                          .replace(f"{stem}.rb", f"{stem}_spec.rb"))
        candidates.append(src_path.replace("lib/", "test/")
                          .replace(f"{stem}.rb", f"test_{stem}.rb"))

    return candidates


# ── Query generation ─────────────────────────────────────────────


@dataclass
class QueryEntry:
    """A query for the ground truth."""

    query_type: str
    query_text: str
    seeds: list[str] = field(default_factory=list)
    pins: list[str] = field(default_factory=list)
    justification: str = ""


def generate_queries(
    issue_title: str,
    issue_body: str,
    min_suff_defs: list[DefEntry],
    changed_paths: list[str],
    diff_text: str,
) -> list[QueryEntry]:
    """Generate query variants from issue text and diff metadata.

    Produces 6+ queries covering the required query types.
    """
    queries: list[QueryEntry] = []

    # Collect seeds and pins from defs
    seeds = list(dict.fromkeys(d.name for d in min_suff_defs))[:8]
    pins = list(dict.fromkeys(changed_paths))[:5]

    # Q_SEMANTIC: natural language description from issue
    semantic_text = _first_meaningful_paragraph(issue_body) or issue_title
    queries.append(QueryEntry(
        query_type="Q_SEMANTIC",
        query_text=semantic_text[:500],
        seeds=[],
        pins=[],
        justification="Issue description as natural language intent",
    ))

    # Q_IDENTIFIER: def names from the diff
    diff_idents = extract_identifiers_from_diff(diff_text)[:10]
    if diff_idents:
        queries.append(QueryEntry(
            query_type="Q_IDENTIFIER",
            query_text=" ".join(diff_idents[:6]),
            seeds=diff_idents[:4],
            pins=[],
            justification="Key identifiers extracted from PR diff",
        ))

    # Q_LEXICAL: title + key terms
    queries.append(QueryEntry(
        query_type="Q_LEXICAL",
        query_text=issue_title,
        seeds=[],
        pins=[],
        justification="Issue title as lexical query",
    ))

    # Q_NAVIGATIONAL: file paths
    if pins:
        queries.append(QueryEntry(
            query_type="Q_NAVIGATIONAL",
            query_text=" ".join(pins),
            seeds=[],
            pins=pins,
            justification="Changed file paths as navigational query",
        ))

    # Q_SEM_IDENT: semantic + identifiers combined
    sem_ident_parts = [issue_title]
    if diff_idents:
        sem_ident_parts.extend(diff_idents[:3])
    queries.append(QueryEntry(
        query_type="Q_SEM_IDENT",
        query_text=" ".join(sem_ident_parts),
        seeds=seeds[:3],
        pins=[],
        justification="Issue title combined with key identifiers",
    ))

    # Q_FULL: full issue body
    full_text = f"{issue_title}\n{issue_body}".strip()
    queries.append(QueryEntry(
        query_type="Q_FULL",
        query_text=full_text[:800],
        seeds=seeds[:4],
        pins=pins[:3],
        justification="Full issue text with seeds and pins",
    ))

    # Q_STRUCTURAL: if we have class/module names
    structural_names = [d.name for d in min_suff_defs if d.kind in ("class", "module", "struct", "interface", "trait")]
    if structural_names:
        queries.append(QueryEntry(
            query_type="Q_STRUCTURAL",
            query_text=" ".join(structural_names[:4]),
            seeds=structural_names[:4],
            pins=[],
            justification="Class/module names from changed definitions",
        ))

    # Q_IDENT_NAV: identifiers + paths
    if diff_idents and pins:
        queries.append(QueryEntry(
            query_type="Q_IDENT_NAV",
            query_text=" ".join(diff_idents[:3] + pins[:2]),
            seeds=diff_idents[:3],
            pins=pins[:2],
            justification="Identifiers with file path navigation",
        ))

    return queries


def _first_meaningful_paragraph(text: str) -> str:
    """Extract the first non-trivial paragraph from issue text."""
    # Strip markdown headers, code blocks, and short lines
    lines: list[str] = []
    in_code_block = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if stripped.startswith("#"):
            continue
        if len(stripped) < 10:
            if lines:
                break  # End of paragraph
            continue
        lines.append(stripped)

    return " ".join(lines) if lines else text[:300]


# ── Task JSON assembly ───────────────────────────────────────────


def classify_complexity(
    file_diffs: list[FileDiff],
    min_suff_defs: list[DefEntry],
) -> str:
    """Classify task complexity from diff statistics."""
    n_files = len([fd for fd in file_diffs if not fd.is_deleted])
    n_defs = len(min_suff_defs)

    if n_files <= 2 and n_defs <= 3:
        return "narrow"
    elif n_files <= 5 and n_defs <= 8:
        return "medium"
    else:
        return "wide"


def assemble_task_json(
    task_id: str,
    pr_number: int,
    pr_title: str,
    issue_body: str,
    diff_text: str,
    file_diffs: list[FileDiff],
    min_suff: list[DefEntry],
    thrash_prev: list[DefEntry],
    excluded: list[DefEntry],
    queries: list[QueryEntry],
) -> dict[str, Any]:
    """Assemble a complete task JSON matching the GT schema."""
    complexity = classify_complexity(file_diffs, min_suff)

    return {
        "task_id": task_id,
        "task_complexity": complexity,
        "task_text": issue_body[:2000],
        "diff": diff_text,
        "solve_notes": f"PR #{pr_number}: {pr_title}",
        "confidence": "high",
        "source": "pr-mining",
        "minimum_sufficient_defs": [
            {
                "path": d.path,
                "name": d.name,
                "kind": d.kind,
                "start_line": d.start_line,
                "reason": d.reason,
            }
            for d in min_suff
        ],
        "thrash_preventing_defs": [
            {
                "path": d.path,
                "name": d.name,
                "kind": d.kind,
                "start_line": d.start_line,
                "reason": d.reason,
            }
            for d in thrash_prev
        ],
        "tier_difference_reasoning": (
            "minimum_sufficient = defs overlapping changed hunks in the PR diff; "
            "thrash_preventing = same-file context defs + test file defs "
            "that the developer needed to read but not edit"
        ),
        "excluded_defs": [
            {
                "path": d.path,
                "name": d.name,
                "kind": d.kind,
                "start_line": d.start_line,
                "reason": d.reason,
            }
            for d in excluded
        ],
        "queries": [
            {
                "query_type": q.query_type,
                "query_text": q.query_text,
                "seeds": q.seeds,
                "pins": q.pins,
                "justification": q.justification,
            }
            for q in queries
        ],
    }
