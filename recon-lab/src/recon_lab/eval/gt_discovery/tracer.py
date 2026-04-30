"""Tool call trace collector for GT discovery.

Captures every file/def/span the agent accesses during task solving,
resolved against the worktree index to produce def-level granularity.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AccessRecord:
    """One def/span the agent accessed during solving."""

    def_uid: str | None
    path: str
    name: str | None
    kind: str | None
    start_line: int | None
    end_line: int | None
    access_type: str  # recon_result | file_read | impact_result | grep_hit
    turn: int
    reason: str | None = None


@dataclass
class TraceCollector:
    """Collects all file/def accesses during an agent session.

    The resolver callback is injected at init time, binding it to the
    worktree index (avoids passing DB handles through every tool).
    """

    _resolve_defs: Any = None  # Callable[[str, int, int], list[dict]]
    _turn: int = 0
    records: list[AccessRecord] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    _start_ts: float = field(default_factory=time.monotonic)

    def advance_turn(self) -> None:
        self._turn += 1

    def log_recon_results(self, results: list[dict[str, Any]]) -> None:
        """Log every def returned by a recon() call."""
        for span in results:
            self.records.append(AccessRecord(
                def_uid=span.get("def_uid"),
                path=span.get("path", ""),
                name=span.get("name"),
                kind=span.get("kind"),
                start_line=span.get("start_line"),
                end_line=span.get("end_line"),
                access_type="recon_result",
                turn=self._turn,
            ))

    def log_impact_results(self, target: str, refs: list[dict[str, Any]]) -> None:
        """Log defs found via impact analysis."""
        for ref in refs:
            self.records.append(AccessRecord(
                def_uid=ref.get("def_uid"),
                path=ref.get("path", ""),
                name=ref.get("name"),
                kind=ref.get("kind"),
                start_line=ref.get("start_line"),
                end_line=ref.get("end_line"),
                access_type="impact_result",
                turn=self._turn,
                reason=f"impact:{target}",
            ))

    def log_file_read(self, path: str, start_line: int, end_line: int) -> None:
        """Resolve a file read span to overlapping defs from the index."""
        if self._resolve_defs is None:
            # Fallback: record the raw span without def resolution
            self.records.append(AccessRecord(
                def_uid=None,
                path=path,
                name=None,
                kind=None,
                start_line=start_line,
                end_line=end_line,
                access_type="file_read",
                turn=self._turn,
            ))
            return

        defs = self._resolve_defs(path, start_line, end_line)
        if not defs:
            self.records.append(AccessRecord(
                def_uid=None,
                path=path,
                name=None,
                kind=None,
                start_line=start_line,
                end_line=end_line,
                access_type="file_read",
                turn=self._turn,
            ))
        else:
            for d in defs:
                self.records.append(AccessRecord(
                    def_uid=d.get("def_uid"),
                    path=d.get("path", path),
                    name=d.get("name"),
                    kind=d.get("kind"),
                    start_line=d.get("start_line"),
                    end_line=d.get("end_line"),
                    access_type="file_read",
                    turn=self._turn,
                ))

    def log_grep_hit(self, path: str, line: int) -> None:
        """Log a grep match line, resolving to the enclosing def."""
        self.log_file_read(path, line, line)
        # Reclassify the last record
        if self.records and self.records[-1].access_type == "file_read":
            rec = self.records.pop()
            self.records.append(AccessRecord(
                def_uid=rec.def_uid,
                path=rec.path,
                name=rec.name,
                kind=rec.kind,
                start_line=rec.start_line,
                end_line=rec.end_line,
                access_type="grep_hit",
                turn=rec.turn,
            ))

    def log_tool_call(self, name: str, args: dict[str, Any], result_len: int) -> None:
        """Log a raw tool invocation for auditing."""
        self.tool_calls.append({
            "name": name,
            "args": args,
            "result_len": result_len,
            "turn": self._turn,
            "elapsed_sec": round(time.monotonic() - self._start_ts, 2),
        })

    def touched_def_uids(self) -> set[str]:
        """Unique def UIDs the agent accessed (excluding unresolved)."""
        return {r.def_uid for r in self.records if r.def_uid}

    def touched_paths(self) -> set[str]:
        """Unique file paths accessed."""
        return {r.path for r in self.records if r.path}

    def to_dict(self) -> dict[str, Any]:
        """Serialize full trace for storage in TaskState."""
        return {
            "records": [
                {
                    "def_uid": r.def_uid,
                    "path": r.path,
                    "name": r.name,
                    "kind": r.kind,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "access_type": r.access_type,
                    "turn": r.turn,
                    "reason": r.reason,
                }
                for r in self.records
            ],
            "tool_calls": self.tool_calls,
            "unique_def_uids": sorted(self.touched_def_uids()),
            "unique_paths": sorted(self.touched_paths()),
            "total_turns": self._turn,
        }
