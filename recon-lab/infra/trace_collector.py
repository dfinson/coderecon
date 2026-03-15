"""Passive trace collector for Copilot SDK sessions.

Attaches to a CopilotSession via ``session.on()`` and records every
event to an append-only JSON-lines log.  Zero agent cooperation
required — the collector observes the SDK event stream and writes
structured records that downstream ``trace_to_candidates`` can
consume.

Usage (inside gt_orchestrator)::

    collector = TraceCollector(repo_id, heading_id, output_dir)
    session.on(collector.handle_event)
    # ... session.send() ...
    collector.flush()          # after session ends
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


# Event type constants we care about (values from copilot.generated.session_events)
_TOOL_START = "tool.execution_start"
_TOOL_COMPLETE = "tool.execution_complete"
_TOOL_PARTIAL = "tool.execution_partial_result"
_USER_MSG = "user.message"
_ASSISTANT_MSG = "assistant.message"
_SKILL_INVOKED = "skill.invoked"
_EXT_TOOL_REQ = "external_tool.requested"
_EXT_TOOL_DONE = "external_tool.completed"

# Tool names whose arguments contain file paths / line ranges
_FILE_READ_TOOLS = frozenset({
    "read_file",
    "readFile",
    "str_replace_editor",
    "editFile",
    "insertCodeBlock",
})

_SEARCH_TOOLS = frozenset({
    "grep_search",
    "grepSearch",
    "semantic_search",
    "semanticSearch",
    "file_search",
    "fileSearch",
    "codebase_search",
    "codebaseSearch",
})


@dataclass
class TraceRecord:
    """One emitted event distilled to what downstream processing needs."""

    timestamp: str
    event_type: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments: dict[str, Any] | None = None
    success: bool | None = None
    # For file reads: extracted path and range
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    # For search tools: the query string
    search_query: str | None = None
    # For user.message attachments
    attachments: list[dict[str, Any]] | None = None
    # For assistant.message tool_requests
    tool_requests: list[dict[str, Any]] | None = None
    # For skill.invoked
    skill_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
        }
        for attr in (
            "tool_name", "tool_call_id", "arguments", "success",
            "file_path", "start_line", "end_line",
            "search_query", "attachments", "tool_requests", "skill_path",
        ):
            val = getattr(self, attr)
            if val is not None:
                d[attr] = val
        return d


class TraceCollector:
    """Passive observer that records SDK session events.

    Attach via ``session.on(collector.handle_event)`` *before* the first
    ``session.send()`` call.  After the session ends, call ``flush()``
    to write the collected records to disk.
    """

    def __init__(self, repo_id: str, heading_id: str, output_dir: Path) -> None:
        self.repo_id = repo_id
        self.heading_id = heading_id
        self.output_dir = output_dir
        self._records: list[TraceRecord] = []

    # ── public API ───────────────────────────────────────────────

    def handle_event(self, event: Any) -> None:
        """Callback for ``session.on()``.  Accepts any SessionEvent."""
        etype = event.type.value if hasattr(event.type, "value") else str(event.type)
        data = event.data

        if etype == _TOOL_START:
            self._on_tool_start(etype, data, event)
        elif etype == _TOOL_COMPLETE:
            self._on_tool_complete(etype, data, event)
        elif etype == _USER_MSG:
            self._on_user_message(etype, data, event)
        elif etype == _ASSISTANT_MSG:
            self._on_assistant_message(etype, data, event)
        elif etype == _SKILL_INVOKED:
            self._on_skill_invoked(etype, data, event)
        elif etype in (_EXT_TOOL_REQ, _EXT_TOOL_DONE):
            self._on_external_tool(etype, data, event)
        # All other event types are ignored — we don't need them for
        # context reconstruction.

    def flush(self) -> Path:
        """Write collected records to ``{output_dir}/{heading_id}.jsonl``.

        Returns the path written to.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.output_dir / f"{self.heading_id}.jsonl"
        with open(out_path, "w") as f:
            for rec in self._records:
                f.write(json.dumps(rec.to_dict()) + "\n")
        return out_path

    @property
    def record_count(self) -> int:
        return len(self._records)

    # ── internal handlers ────────────────────────────────────────

    def _ts(self, event: Any) -> str:
        ts = getattr(event, "timestamp", None)
        if isinstance(ts, datetime):
            return ts.isoformat()
        return datetime.now().isoformat()

    def _on_tool_start(self, etype: str, data: Any, event: Any) -> None:
        tool_name = getattr(data, "tool_name", None) or ""
        tool_call_id = getattr(data, "tool_call_id", None)
        arguments = getattr(data, "arguments", None)
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {"raw": arguments}

        rec = TraceRecord(
            timestamp=self._ts(event),
            event_type=etype,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=arguments if isinstance(arguments, dict) else None,
        )

        # Extract file path + line range for file-reading tools
        if isinstance(arguments, dict):
            fp = arguments.get("filePath") or arguments.get("file_path") or arguments.get("path")
            if fp:
                rec.file_path = fp
                rec.start_line = arguments.get("startLine") or arguments.get("start_line")
                rec.end_line = arguments.get("endLine") or arguments.get("end_line")

            # Extract search query
            sq = arguments.get("query") or arguments.get("pattern") or arguments.get("search")
            if sq:
                rec.search_query = sq

        self._records.append(rec)

    def _on_tool_complete(self, etype: str, data: Any, event: Any) -> None:
        rec = TraceRecord(
            timestamp=self._ts(event),
            event_type=etype,
            tool_name=getattr(data, "tool_name", None),
            tool_call_id=getattr(data, "tool_call_id", None),
            success=getattr(data, "success", None),
        )
        self._records.append(rec)

    def _on_user_message(self, etype: str, data: Any, event: Any) -> None:
        attachments_raw = getattr(data, "attachments", None)
        if not attachments_raw:
            return  # No attachments → nothing to trace

        attachments = []
        for att in attachments_raw:
            entry: dict[str, Any] = {}
            for attr in ("type", "path", "file_path", "display_name"):
                val = getattr(att, attr, None)
                if val is not None:
                    entry[attr] = val
            lr = getattr(att, "line_range", None)
            if lr is not None:
                entry["start_line"] = getattr(lr, "start", None)
                entry["end_line"] = getattr(lr, "end", None)
            if entry:
                attachments.append(entry)

        if attachments:
            self._records.append(TraceRecord(
                timestamp=self._ts(event),
                event_type=etype,
                attachments=attachments,
            ))

    def _on_assistant_message(self, etype: str, data: Any, event: Any) -> None:
        tool_reqs = getattr(data, "tool_requests", None)
        if not tool_reqs:
            return  # No tool requests → nothing to trace

        requests = []
        for tr in tool_reqs:
            entry: dict[str, Any] = {
                "name": getattr(tr, "name", ""),
                "tool_call_id": getattr(tr, "tool_call_id", ""),
            }
            args = getattr(tr, "arguments", None)
            if args is not None:
                entry["arguments"] = args
            requests.append(entry)

        self._records.append(TraceRecord(
            timestamp=self._ts(event),
            event_type=etype,
            tool_requests=requests,
        ))

    def _on_skill_invoked(self, etype: str, data: Any, event: Any) -> None:
        self._records.append(TraceRecord(
            timestamp=self._ts(event),
            event_type=etype,
            skill_path=getattr(data, "path", None),
        ))

    def _on_external_tool(self, etype: str, data: Any, event: Any) -> None:
        self._records.append(TraceRecord(
            timestamp=self._ts(event),
            event_type=etype,
            tool_name=getattr(data, "tool_name", None),
            tool_call_id=getattr(data, "tool_call_id", None),
            arguments=getattr(data, "arguments", None) if etype == _EXT_TOOL_REQ else None,
            success=getattr(data, "success", None) if etype == _EXT_TOOL_DONE else None,
        ))
