"""Framework adapters — export SDK functions as OpenAI / LangChain tools.

Each adapter produces tool definitions that agent frameworks can consume
natively, with the ``repo`` parameter pre-bound so agents don't need to
specify it per call.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from coderecon.sdk.client import CodeRecon


# ---------------------------------------------------------------------------
# Tool metadata — shared between adapters
# ---------------------------------------------------------------------------

_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "recon",
        "description": "Task-aware context retrieval — returns ranked semantic spans with code.",
        "method": "recon",
        "params": {
            "task": {"type": "string", "description": "Natural language task description."},
            "seeds": {"type": "array", "items": {"type": "string"}, "description": "Symbol names to seed retrieval.", "default": []},
            "pins": {"type": "array", "items": {"type": "string"}, "description": "File paths to pin as relevant.", "default": []},
        },
        "required": ["task"],
    },
    {
        "name": "recon_map",
        "description": "Repository structure map — file tree, languages, entry points.",
        "method": "recon_map",
        "params": {},
        "required": [],
    },
    {
        "name": "recon_impact",
        "description": "Find all references to a symbol/file for read-only impact analysis.",
        "method": "recon_impact",
        "params": {
            "target": {"type": "string", "description": "Symbol or path to analyze."},
            "justification": {"type": "string", "description": "Why you need impact analysis."},
            "include_comments": {"type": "boolean", "description": "Include comment references.", "default": True},
        },
        "required": ["target", "justification"],
    },
    {
        "name": "recon_understand",
        "description": "Full codebase narrative briefing — structure, PageRank, communities, coverage, lint.",
        "method": "recon_understand",
        "params": {},
        "required": [],
    },
    {
        "name": "semantic_diff",
        "description": "Structural change summary between two states.",
        "method": "semantic_diff",
        "params": {
            "base": {"type": "string", "description": "Base ref (commit, branch, tag).", "default": "HEAD"},
            "target": {"type": "string", "description": "Target ref (None = working tree)."},
            "paths": {"type": "array", "items": {"type": "string"}, "description": "Limit to specific paths."},
        },
        "required": [],
    },
    {
        "name": "refactor_rename",
        "description": "Rename a symbol across the codebase.",
        "method": "refactor_rename",
        "params": {
            "symbol": {"type": "string", "description": "Symbol name to rename."},
            "new_name": {"type": "string", "description": "New name for the symbol."},
            "justification": {"type": "string", "description": "Why you are renaming."},
            "include_comments": {"type": "boolean", "description": "Include comment references.", "default": True},
        },
        "required": ["symbol", "new_name", "justification"],
    },
    {
        "name": "refactor_move",
        "description": "Move a file/module, updating imports.",
        "method": "refactor_move",
        "params": {
            "from_path": {"type": "string", "description": "Source file path."},
            "to_path": {"type": "string", "description": "Destination file path."},
            "justification": {"type": "string", "description": "Why you are moving."},
            "include_comments": {"type": "boolean", "description": "Include comment references.", "default": True},
        },
        "required": ["from_path", "to_path", "justification"],
    },
    {
        "name": "refactor_commit",
        "description": "Apply or inspect a previewed refactoring.",
        "method": "refactor_commit",
        "params": {
            "refactor_id": {"type": "string", "description": "ID of the refactoring."},
            "inspect_path": {"type": "string", "description": "File to inspect instead of applying."},
            "context_lines": {"type": "integer", "description": "Context lines around matches.", "default": 2},
        },
        "required": ["refactor_id"],
    },
    {
        "name": "refactor_cancel",
        "description": "Cancel a pending refactoring.",
        "method": "refactor_cancel",
        "params": {
            "refactor_id": {"type": "string", "description": "ID of the refactoring to cancel."},
        },
        "required": ["refactor_id"],
    },
    {
        "name": "checkpoint",
        "description": "Lint, test, and optionally commit+push in one call.",
        "method": "checkpoint",
        "params": {
            "changed_files": {"type": "array", "items": {"type": "string"}, "description": "Files you changed."},
            "lint": {"type": "boolean", "description": "Run linting.", "default": True},
            "autofix": {"type": "boolean", "description": "Apply lint auto-fixes.", "default": True},
            "tests": {"type": "boolean", "description": "Run affected tests.", "default": True},
            "commit_message": {"type": "string", "description": "If set and checks pass, auto-commit."},
            "push": {"type": "boolean", "description": "Push after commit.", "default": False},
        },
        "required": ["changed_files"],
    },
    {
        "name": "graph_cycles",
        "description": "Detect circular dependencies using Tarjan's algorithm.",
        "method": "graph_cycles",
        "params": {
            "level": {"type": "string", "description": "'file' or 'def'.", "default": "file"},
        },
        "required": [],
    },
    {
        "name": "graph_communities",
        "description": "Detect module communities using Louvain algorithm.",
        "method": "graph_communities",
        "params": {
            "level": {"type": "string", "description": "'file' or 'def'.", "default": "file"},
            "resolution": {"type": "number", "description": "Louvain resolution.", "default": 1.0},
        },
        "required": [],
    },
]


# ---------------------------------------------------------------------------
# OpenAI function calling
# ---------------------------------------------------------------------------


def as_openai_tools(
    sdk: "CodeRecon",
    *,
    repo: str,
    worktree: str | None = None,
) -> list[dict[str, Any]]:
    """Return OpenAI-compatible tool definitions with ``repo`` pre-bound.

    Returns a list of dicts suitable for ``tools=`` in the OpenAI API.
    Each dict has ``type``, ``function`` (name, description, parameters),
    and an ``_execute`` key with the bound async callable.
    """
    tools: list[dict[str, Any]] = []

    for tdef in _TOOL_DEFS:
        method_name = tdef["method"]
        sdk_method = getattr(sdk, method_name, None)
        if sdk_method is None:
            continue

        # Build JSON schema for params (excluding repo/worktree — pre-bound)
        properties: dict[str, Any] = {}
        for pname, pdef in tdef["params"].items():
            prop: dict[str, Any] = {"type": pdef["type"], "description": pdef["description"]}
            if "items" in pdef:
                prop["items"] = pdef["items"]
            if "default" in pdef:
                prop["default"] = pdef["default"]
            properties[pname] = prop

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": tdef["required"],
        }

        # Bind repo (and worktree) into the callable
        bound = functools.partial(sdk_method, repo, worktree=worktree) if _takes_repo(sdk_method) else sdk_method

        tools.append({
            "type": "function",
            "function": {
                "name": tdef["name"],
                "description": tdef["description"],
                "parameters": schema,
            },
            "_execute": bound,
        })

    return tools


def _takes_repo(method: Callable[..., Any]) -> bool:
    """Check if the method has a 'repo' parameter."""
    try:
        sig = inspect.signature(method)
        return "repo" in sig.parameters
    except (ValueError, TypeError):
        return True  # assume yes


# ---------------------------------------------------------------------------
# LangChain adapter (optional dependency)
# ---------------------------------------------------------------------------


def as_langchain_tools(
    sdk: "CodeRecon",
    *,
    repo: str,
    worktree: str | None = None,
) -> list[Any]:
    """Return LangChain ``StructuredTool`` instances.

    Requires ``langchain-core`` to be installed.
    """
    try:
        from langchain_core.tools import StructuredTool
    except ImportError as exc:
        raise ImportError(
            "langchain-core is required for as_langchain_tools(). "
            "Install it with: uv add langchain-core"
        ) from exc

    tools = []
    for tdef in _TOOL_DEFS:
        method_name = tdef["method"]
        sdk_method = getattr(sdk, method_name, None)
        if sdk_method is None:
            continue

        bound = functools.partial(sdk_method, repo, worktree=worktree) if _takes_repo(sdk_method) else sdk_method

        tool = StructuredTool.from_function(
            coroutine=bound,
            name=tdef["name"],
            description=tdef["description"],
        )
        tools.append(tool)

    return tools
