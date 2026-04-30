"""Inspect AI @tool wrappers for GT discovery.

Bridges CodeRecon SDK methods into Inspect AI tool format with
trace collection. All tools are read-only — the agent explores
but does not modify the codebase.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from inspect_ai.tool import Tool, tool

from recon_lab.eval.gt_discovery.tracer import TraceCollector

# Module-level state injected per-sample by the solver before the agent loop.
_CTX: dict[str, Any] = {}


def bind_tools_context(
    *,
    ctx: Any,
    repo: str,
    worktree_root: Path,
    tracer: TraceCollector,
) -> None:
    """Inject per-sample context for tool execution."""
    _CTX["app_ctx"] = ctx
    _CTX["repo"] = repo
    _CTX["worktree_root"] = worktree_root
    _CTX["tracer"] = tracer


def _tracer() -> TraceCollector:
    return _CTX["tracer"]


def _worktree_root() -> Path:
    return _CTX["worktree_root"]


@tool
def recon_search() -> Tool:
    """Task-aware context retrieval — returns ranked code spans."""

    async def execute(task: str, seeds: list[str] | None = None, pins: list[str] | None = None) -> str:
        """Search for code relevant to a task description.

        Args:
            task: Natural language description of what you're looking for.
            seeds: Optional symbol names to seed retrieval.
            pins: Optional file paths to pin as relevant context.
        """
        from coderecon.mcp.tools.recon.raw_signals import raw_signals_pipeline

        ctx = _CTX["app_ctx"]
        raw = await raw_signals_pipeline(ctx, task, seeds=seeds or None, pins=pins or None)
        candidates = raw.get("candidates", [])

        tracer = _tracer()
        tracer.log_recon_results(candidates)
        tracer.log_tool_call("recon_search", {"task": task, "seeds": seeds, "pins": pins}, len(candidates))

        # Format results for the agent
        lines = [f"Found {len(candidates)} relevant definitions:\n"]
        for i, c in enumerate(candidates[:30]):
            path = c.get("path", "")
            name = c.get("name", "")
            kind = c.get("kind", "")
            start = c.get("start_line", 0)
            end = c.get("end_line", 0)
            lines.append(f"  {i + 1}. [{kind}] {path}:{name} (L{start}-{end})")
        if len(candidates) > 30:
            lines.append(f"  ... and {len(candidates) - 30} more")
        return "\n".join(lines)

    return execute


@tool
def recon_impact() -> Tool:
    """Find all references to a symbol for impact analysis."""

    async def execute(target: str, justification: str) -> str:
        """Find all usages of a symbol across the codebase.

        Args:
            target: Symbol name or file path to analyze.
            justification: Why you need to see the impact.
        """
        from coderecon.mcp.tools.recon.impact import impact_pipeline

        ctx = _CTX["app_ctx"]
        try:
            result = await impact_pipeline(ctx, target, justification)
        except Exception:
            return f"Impact analysis failed for '{target}'"

        refs = result.get("references", [])
        tracer = _tracer()
        tracer.log_impact_results(target, refs)
        tracer.log_tool_call("recon_impact", {"target": target}, len(refs))

        lines = [f"References to '{target}': {len(refs)}\n"]
        for ref in refs[:25]:
            path = ref.get("path", "")
            line = ref.get("start_line", 0)
            name = ref.get("name", "")
            lines.append(f"  {path}:{line} ({name})")
        if len(refs) > 25:
            lines.append(f"  ... and {len(refs) - 25} more")
        return "\n".join(lines)

    return execute


@tool
def recon_map() -> Tool:
    """Get repository structure overview."""

    async def execute() -> str:
        """Get the file tree, languages, and entry points of the repository."""
        from coderecon.mcp.tools.recon.understand import map_pipeline

        ctx = _CTX["app_ctx"]
        try:
            result = await map_pipeline(ctx)
        except Exception:
            return "Map unavailable"

        tracer = _tracer()
        tracer.log_tool_call("recon_map", {}, 1)

        # Format a useful subset
        lines = []
        for key in ("languages", "top_dirs", "entry_points"):
            val = result.get(key)
            if val:
                lines.append(f"{key}: {val}")
        return "\n".join(lines) if lines else str(result)[:2000]

    return execute


@tool
def read_file() -> Tool:
    """Read a file or file span from the repository."""

    async def execute(path: str, start_line: int = 1, end_line: int = -1) -> str:
        """Read file content.

        Args:
            path: Relative file path within the repository.
            start_line: First line to read (1-indexed).
            end_line: Last line to read (-1 = end of file).
        """
        root = _worktree_root()
        full_path = root / path

        if not full_path.is_file():
            return f"File not found: {path}"

        try:
            text = full_path.read_text(errors="replace")
        except OSError as e:
            return f"Cannot read {path}: {e}"

        file_lines = text.splitlines()
        total = len(file_lines)

        if end_line == -1:
            end_line = total
        start_line = max(1, start_line)
        end_line = min(end_line, total)

        chunk = file_lines[start_line - 1:end_line]
        content = "\n".join(chunk)

        # Truncate very large reads
        if len(content) > 12000:
            content = content[:12000] + "\n... [truncated]"

        tracer = _tracer()
        tracer.log_file_read(path, start_line, end_line)
        tracer.log_tool_call("read_file", {"path": path, "start_line": start_line, "end_line": end_line}, end_line - start_line + 1)

        return f"```{path} (L{start_line}-{end_line} of {total})\n{content}\n```"

    return execute


@tool
def grep_search() -> Tool:
    """Search for a pattern in the repository."""

    async def execute(pattern: str, paths: list[str] | None = None) -> str:
        """Grep for a regex pattern across repository files.

        Args:
            pattern: Regex pattern to search for.
            paths: Optional list of paths/globs to restrict search.
        """
        import re
        import subprocess

        root = _worktree_root()

        cmd = ["grep", "-rn", "--include=*.py", "--include=*.ts", "--include=*.js",
               "--include=*.go", "--include=*.rs", "--include=*.java", "--include=*.cs",
               "--include=*.swift", "--include=*.rb", "--include=*.php",
               "--include=*.toml", "--include=*.yml", "--include=*.yaml",
               "--include=*.json", "--include=*.md",
               "-E", pattern]

        if paths:
            for p in paths:
                cmd.extend(["--include", p])

        try:
            proc = subprocess.run(
                cmd,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            return "Grep timed out or failed"

        output = proc.stdout
        hit_lines = output.splitlines()[:50]

        tracer = _tracer()
        # Log each hit for def resolution
        for hit in hit_lines:
            parts = hit.split(":", 2)
            if len(parts) >= 2:
                hit_path = parts[0]
                try:
                    hit_line = int(parts[1])
                    tracer.log_grep_hit(hit_path, hit_line)
                except ValueError:
                    pass
        tracer.log_tool_call("grep_search", {"pattern": pattern, "paths": paths}, len(hit_lines))

        if not hit_lines:
            return f"No matches for pattern: {pattern}"

        result = "\n".join(hit_lines)
        if len(output.splitlines()) > 50:
            result += f"\n... ({len(output.splitlines()) - 50} more matches)"
        return result

    return execute


@tool
def list_dir() -> Tool:
    """List directory contents."""

    async def execute(path: str = ".") -> str:
        """List files and subdirectories at a path.

        Args:
            path: Relative directory path (default: repository root).
        """
        root = _worktree_root()
        target = root / path

        if not target.is_dir():
            return f"Not a directory: {path}"

        entries = sorted(target.iterdir())
        lines = []
        for entry in entries[:100]:
            rel = entry.relative_to(root)
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"  {rel}{suffix}")

        tracer = _tracer()
        tracer.log_tool_call("list_dir", {"path": path}, len(lines))

        if len(entries) > 100:
            lines.append(f"  ... ({len(entries) - 100} more)")
        return "\n".join(lines) if lines else "(empty directory)"

    return execute


def all_tools() -> list[Tool]:
    """Return all GT discovery tools."""
    return [
        recon_search(),
        recon_impact(),
        recon_map(),
        read_file(),
        grep_search(),
        list_dir(),
    ]
