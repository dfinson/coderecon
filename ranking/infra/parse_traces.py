"""Parse VS Code chat export JSON into per-task tool-use traces.

Usage:
    python parse_traces.py <chat_export.json> [--output traces.jsonl]

Reads a VS Code chat export (from workbench.action.chat.export),
finds START_EVAL_TASK-{id} / END_EVAL_TASK-{id} terminal markers,
and extracts the tool calls between them into a JSONL file with
one entry per task.

The export format is a JSON object with a "requests" array. Each
request has a "response" array of parts. Parts with
kind=toolInvocationSerialized contain tool call data.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ToolCall:
    """A single tool invocation within a task trace."""

    seq: int
    tool_id: str
    invocation_message: str
    command_line: str | None = None
    file_path: str | None = None
    is_confirmed: bool = True
    is_complete: bool = True


@dataclass
class TaskTrace:
    """Complete trace for one task."""

    task_id: str
    request_index: int
    start_part_index: int
    end_part_index: int
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def tool_count(self) -> int:
        return len(self.tool_calls)

    @property
    def unique_tools(self) -> list[str]:
        return sorted(set(tc.tool_id for tc in self.tool_calls))

    @property
    def files_read(self) -> list[str]:
        return [tc.file_path for tc in self.tool_calls if tc.file_path and "read" in tc.tool_id.lower()]

    @property
    def searches(self) -> list[str]:
        return [tc.invocation_message for tc in self.tool_calls if "search" in tc.tool_id.lower() or "find" in tc.tool_id.lower()]


def _extract_command_line(tsd: dict) -> str | None:
    """Extract command line from toolSpecificData."""
    cmd = tsd.get("commandLine")
    if isinstance(cmd, dict):
        return cmd.get("original", cmd.get("toolEdited", ""))
    if isinstance(cmd, str):
        return cmd
    return None


def _extract_file_path(part: dict) -> str | None:
    """Extract file path from a read_file tool call."""
    inv = part.get("invocationMessage", "")
    if isinstance(inv, dict):
        inv = inv.get("value", "")
    if not isinstance(inv, str):
        return None
    # Pattern: "Reading [](file:///path/to/file#line), lines X to Y"
    if "file:///" in inv:
        start = inv.index("file:///") + len("file:///")
        end = inv.find(")", start)
        if end == -1:
            end = inv.find(",", start)
        if end == -1:
            end = len(inv)
        path = inv[start:end]
        # Strip line anchors
        if "#" in path:
            path = path[: path.index("#")]
        return "/" + path
    return None


def _extract_invocation_message(part: dict) -> str:
    """Extract the human-readable invocation message."""
    inv = part.get("invocationMessage", "")
    if isinstance(inv, dict):
        return inv.get("value", "")
    if isinstance(inv, str):
        return inv
    return ""


def _parse_marker(cmd: str) -> tuple[str, str] | None:
    """Parse START_EVAL_TASK-{id} or END_EVAL_TASK-{id} from a command string.

    Returns ("start", task_id) or ("end", task_id), or None if not a marker.
    """
    if "START_EVAL_TASK-" in cmd and "END_EVAL_TASK-" in cmd:
        # Combined marker — treat as invalid (design mandates separate commands)
        return None

    for prefix, kind in [("START_EVAL_TASK-", "start"), ("END_EVAL_TASK-", "end")]:
        if prefix in cmd:
            rest = cmd.split(prefix, 1)[1]
            # Strip quotes, whitespace
            task_id = rest.strip().strip('"').strip("'").strip()
            return (kind, task_id)
    return None


def parse_chat_export(data: dict) -> list[TaskTrace]:
    """Parse a VS Code chat export into per-task traces.

    Args:
        data: Parsed JSON from VS Code chat export.

    Returns:
        List of TaskTrace objects, one per detected task.
    """
    requests = data.get("requests", [])
    traces: list[TaskTrace] = []

    for req_idx, req in enumerate(requests):
        response = req.get("response", [])
        if not isinstance(response, list):
            continue

        current_task: str | None = None
        current_trace: TaskTrace | None = None
        seq = 0

        for part_idx, part in enumerate(response):
            if not isinstance(part, dict):
                continue

            kind = part.get("kind", "")

            if kind != "toolInvocationSerialized":
                continue

            tool_id = part.get("toolId", "")
            tsd = part.get("toolSpecificData", {})
            if not isinstance(tsd, dict):
                tsd = {}

            cmd = _extract_command_line(tsd)

            # Check for markers (only in terminal commands)
            if tool_id == "run_in_terminal" and cmd:
                marker = _parse_marker(cmd)
                if marker:
                    marker_kind, task_id = marker
                    if marker_kind == "start":
                        current_task = task_id
                        current_trace = TaskTrace(
                            task_id=task_id,
                            request_index=req_idx,
                            start_part_index=part_idx,
                            end_part_index=-1,
                        )
                        seq = 0
                        continue
                    elif marker_kind == "end":
                        if current_trace and current_trace.task_id == task_id:
                            current_trace.end_part_index = part_idx
                            traces.append(current_trace)
                        current_task = None
                        current_trace = None
                        continue

            # If we're inside a task, record this tool call
            if current_task and current_trace:
                seq += 1
                # Normalize is_confirmed — VS Code exports it as {"type": 1} or bool
                confirmed = part.get("isConfirmed", True)
                if isinstance(confirmed, dict):
                    confirmed = confirmed.get("type", 0) == 1
                complete = part.get("isComplete", True)
                if isinstance(complete, dict):
                    complete = complete.get("type", 0) == 1

                tc = ToolCall(
                    seq=seq,
                    tool_id=tool_id,
                    invocation_message=_extract_invocation_message(part),
                    command_line=cmd,
                    file_path=_extract_file_path(part),
                    is_confirmed=bool(confirmed),
                    is_complete=bool(complete),
                )
                current_trace.tool_calls.append(tc)

    return traces


def traces_to_jsonl(traces: list[TaskTrace]) -> str:
    """Convert traces to JSONL format."""
    lines = []
    for trace in traces:
        entry = {
            "task_id": trace.task_id,
            "request_index": trace.request_index,
            "start_part_index": trace.start_part_index,
            "end_part_index": trace.end_part_index,
            "tool_count": trace.tool_count,
            "unique_tools": trace.unique_tools,
            "tool_calls": [asdict(tc) for tc in trace.tool_calls],
        }
        lines.append(json.dumps(entry, ensure_ascii=False))
    return "\n".join(lines) + "\n" if lines else ""


def print_summary(traces: list[TaskTrace]) -> None:
    """Print a human-readable summary of extracted traces."""
    print(f"\n{'='*60}")
    print(f"Extracted {len(traces)} task traces")
    print(f"{'='*60}\n")

    for trace in traces:
        print(f"Task: {trace.task_id}")
        print(f"  Tool calls: {trace.tool_count}")
        print(f"  Tools used: {', '.join(trace.unique_tools)}")
        for tc in trace.tool_calls:
            detail = tc.file_path or tc.command_line or tc.invocation_message
            if len(str(detail)) > 80:
                detail = str(detail)[:77] + "..."
            print(f"    [{tc.seq}] {tc.tool_id}: {detail}")
        print()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python parse_traces.py <chat_export.json> [--output traces.jsonl]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_path = Path(sys.argv[idx + 1])

    with open(input_path) as f:
        data = json.load(f)

    traces = parse_chat_export(data)
    print_summary(traces)

    if output_path:
        output_path.write_text(traces_to_jsonl(traces))
        print(f"Written {len(traces)} traces to {output_path}")
    else:
        # Default output
        default_out = input_path.with_suffix(".traces.jsonl")
        default_out.write_text(traces_to_jsonl(traces))
        print(f"Written {len(traces)} traces to {default_out}")


if __name__ == "__main__":
    main()
