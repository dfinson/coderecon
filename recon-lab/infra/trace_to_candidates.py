"""Convert raw trace logs into candidate def lists via the pre-commit index.

Reads a trace JSONL file (produced by ``trace_collector.py``), extracts
every file-read range, merges overlapping/adjacent ranges per file,
then cross-references against the static ``.recon/index.db`` SQLite
database to find which ``def_facts`` rows overlap with traced ranges.

No coderecon library dependency — uses stdlib ``sqlite3`` directly
against the pre-built index DB.

Usage::

    candidates = trace_to_candidates(
        trace_path=Path("data/python-fastapi/traces/N1.jsonl"),
        index_db_path=Path("clones/ranker-gate/fastapi/.recon/index.db"),
        clone_root=Path("clones/ranker-gate/fastapi"),
    )
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FileRange:
    """A contiguous line range in a file that the agent touched."""

    start_line: int
    end_line: int
    sources: set[str] = field(default_factory=set)  # e.g. {"read_file", "grep_search"}

    def overlaps_or_adjacent(self, other: "FileRange") -> bool:
        return self.start_line <= other.end_line + 1 and other.start_line <= self.end_line + 1

    def merge(self, other: "FileRange") -> "FileRange":
        return FileRange(
            start_line=min(self.start_line, other.start_line),
            end_line=max(self.end_line, other.end_line),
            sources=self.sources | other.sources,
        )


@dataclass
class CandidateDef:
    """A def from the index that overlaps with a traced range."""

    name: str
    kind: str
    file_path: str  # repo-relative
    start_line: int
    end_line: int
    source: str  # how it was discovered (e.g. "read_file", "grep_search")

    @property
    def candidate_key(self) -> str:
        return f"{self.file_path}:{self.kind}:{self.name}:{self.start_line}"


def _extract_ranges(trace_path: Path) -> dict[str, list[FileRange]]:
    """Parse trace JSONL and extract file-read ranges per file path.

    Returns a dict mapping repo-relative file paths to lists of FileRange.
    """
    raw_ranges: dict[str, list[FileRange]] = {}

    with open(trace_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            etype = record.get("event_type", "")

            if etype == "tool.execution_start":
                _extract_tool_ranges(record, raw_ranges)
            elif etype == "user.message":
                _extract_attachment_ranges(record, raw_ranges)

    return raw_ranges


def _extract_tool_ranges(record: dict, out: dict[str, list[FileRange]]) -> None:
    """Extract file path + line range from a tool.execution_start record."""
    fp = record.get("file_path")
    tool_name = record.get("tool_name", "")

    if fp:
        start = record.get("start_line")
        end = record.get("end_line")
        # If no line range specified, the tool read the whole file —
        # use a very wide range that will match all defs in the file
        if start is None:
            start = 1
        if end is None:
            end = 999_999
        out.setdefault(fp, []).append(
            FileRange(start_line=start, end_line=end, sources={tool_name})
        )
    elif tool_name and record.get("arguments"):
        # Some tools embed file paths in arguments differently
        args = record["arguments"]
        if isinstance(args, dict):
            fp = args.get("filePath") or args.get("file_path") or args.get("path")
            if fp:
                start = args.get("startLine") or args.get("start_line") or 1
                end = args.get("endLine") or args.get("end_line") or 999_999
                out.setdefault(fp, []).append(
                    FileRange(start_line=start, end_line=end, sources={tool_name})
                )


def _extract_attachment_ranges(record: dict, out: dict[str, list[FileRange]]) -> None:
    """Extract file paths from user.message attachments."""
    attachments = record.get("attachments", [])
    for att in attachments:
        fp = att.get("path") or att.get("file_path")
        if not fp:
            continue
        start = att.get("start_line") or 1
        end = att.get("end_line") or 999_999
        out.setdefault(fp, []).append(
            FileRange(start_line=start, end_line=end, sources={"attachment"})
        )


def _normalize_path(raw_path: str, clone_root: Path) -> str | None:
    """Convert an absolute or relative path to a repo-relative path.

    Returns None if the path is outside the clone root.
    """
    p = Path(raw_path)
    if p.is_absolute():
        try:
            return str(p.relative_to(clone_root))
        except ValueError:
            return None  # Outside clone root — not a repo file
    return str(p)


def _merge_ranges(ranges: list[FileRange]) -> list[FileRange]:
    """Merge overlapping/adjacent ranges into non-overlapping intervals."""
    if not ranges:
        return []
    sorted_ranges = sorted(ranges, key=lambda r: (r.start_line, r.end_line))
    merged = [sorted_ranges[0]]
    for r in sorted_ranges[1:]:
        if merged[-1].overlaps_or_adjacent(r):
            merged[-1] = merged[-1].merge(r)
        else:
            merged.append(r)
    return merged


def _query_overlapping_defs(
    db: sqlite3.Connection,
    file_path: str,
    ranges: list[FileRange],
) -> list[CandidateDef]:
    """Find all def_facts that overlap with any of the given ranges for a file.

    Uses the pre-built index DB (read-only, no coderecon dependency).
    """
    # First, find the file_id for this path
    row = db.execute(
        "SELECT id FROM files WHERE path = ?", (file_path,)
    ).fetchone()
    if row is None:
        return []
    file_id = row[0]

    # Query all defs in this file
    defs = db.execute(
        "SELECT name, kind, start_line, end_line "
        "FROM def_facts WHERE file_id = ?",
        (file_id,),
    ).fetchall()

    candidates = []
    for name, kind, def_start, def_end in defs:
        for r in ranges:
            # Check overlap: def range and read range intersect
            if def_start <= r.end_line and r.start_line <= def_end:
                candidates.append(CandidateDef(
                    name=name,
                    kind=kind,
                    file_path=file_path,
                    start_line=def_start,
                    end_line=def_end,
                    source=", ".join(sorted(r.sources)),
                ))
                break  # Don't add same def twice for multiple overlapping ranges

    return candidates


def _get_pre_commit_files(db: sqlite3.Connection) -> set[str]:
    """Get the set of all file paths in the index (i.e. files that existed pre-commit)."""
    rows = db.execute("SELECT path FROM files").fetchall()
    return {r[0] for r in rows}


def trace_to_candidates(
    trace_path: Path,
    index_db_path: Path,
    clone_root: Path,
) -> list[CandidateDef]:
    """Main entry point: convert a trace file into a deduplicated candidate list.

    Args:
        trace_path: Path to the JSONL trace file for one task.
        index_db_path: Path to the .recon/index.db SQLite file.
        clone_root: Absolute path to the clone directory (for path normalization).

    Returns:
        Deduplicated list of CandidateDef objects, sorted by file_path + start_line.
    """
    # 1. Extract raw file-read ranges from trace
    raw_ranges = _extract_ranges(trace_path)

    # 2. Normalize paths to repo-relative
    normalized: dict[str, list[FileRange]] = {}
    for raw_path, ranges in raw_ranges.items():
        rel = _normalize_path(raw_path, clone_root)
        if rel is not None:
            normalized.setdefault(rel, []).extend(ranges)

    # 3. Open index DB (read-only)
    db = sqlite3.connect(f"file:{index_db_path}?mode=ro", uri=True)
    try:
        # 4. Get the set of pre-commit files (used to exclude created files)
        pre_commit_files = _get_pre_commit_files(db)

        # 5. For each file, merge ranges then query overlapping defs
        all_candidates: list[CandidateDef] = []
        seen_keys: set[str] = set()

        for file_path, ranges in normalized.items():
            # Exclude files not in the pre-commit index (agent-created files)
            if file_path not in pre_commit_files:
                continue

            merged = _merge_ranges(ranges)
            for cand in _query_overlapping_defs(db, file_path, merged):
                if cand.candidate_key not in seen_keys:
                    seen_keys.add(cand.candidate_key)
                    all_candidates.append(cand)
    finally:
        db.close()

    # 6. Sort by file path, then start line
    all_candidates.sort(key=lambda c: (c.file_path, c.start_line))
    return all_candidates


def candidates_to_json(candidates: list[CandidateDef]) -> list[dict[str, Any]]:
    """Serialize candidates to JSON-friendly dicts for the analyst agent."""
    return [
        {
            "candidate_key": c.candidate_key,
            "name": c.name,
            "kind": c.kind,
            "file_path": c.file_path,
            "start_line": c.start_line,
            "end_line": c.end_line,
            "source": c.source,
        }
        for c in candidates
    ]


def write_candidates(
    candidates: list[CandidateDef],
    output_path: Path,
) -> None:
    """Write candidate list to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(candidates_to_json(candidates), f, indent=2)
        f.write("\n")
