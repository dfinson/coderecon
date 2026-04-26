"""Describe MCP tool - unified introspection handler.

Stripped to two actions for v2:
- tool: Get detailed documentation for a specific tool
- error: Get documentation for an error code
"""

# NOTE: no `from __future__ import annotations` — breaks FastMCP+pydantic
# Literal resolution at runtime (same as diff.py).

from typing import TYPE_CHECKING, Any, Literal

from fastmcp import Context
from pydantic import Field

from coderecon.mcp.docs import get_tool_documentation
from coderecon.mcp.errors import ERROR_CATALOG, get_error_documentation

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from coderecon.mcp.context import AppContext


# Core Function (transport-agnostic)


def describe_core(
    *,
    action: str,
    name: str | None = None,
    code: str | None = None,
    available_tool_names: list[str] | None = None,
    tools_dict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Introspection logic (transport-agnostic).

    ``available_tool_names`` and ``tools_dict`` are passed by the MCP
    wrapper so this function doesn't depend on the FastMCP instance.
    """
    if action == "tool":
        if not name:
            return {"error": "tool action requires 'name'", "summary": "error: missing params"}
        doc = get_tool_documentation(name)
        if doc is None:
            available_tools = available_tool_names or []
            if name not in available_tools:
                return {
                    "found": False,
                    "error": f"Tool '{name}' not found",
                    "available_tools": available_tools,
                    "summary": f"tool '{name}' not found",
                }
            tool_spec = (tools_dict or {}).get(name)
            desc = tool_spec.description if tool_spec else "No description"
            return {
                "found": True,
                "name": name,
                "description": desc,
                "extended_docs": False,
                "summary": f"{name}: {desc}",
            }
        return {
            "found": True,
            "extended_docs": True,
            **doc.to_dict(),
            "summary": f"{name}: {doc.description}",
        }

    if action == "error":
        if not code:
            return {"error": "error action requires 'code'", "summary": "error: missing params"}
        err_doc = get_error_documentation(code)
        if err_doc is None:
            return {
                "found": False,
                "error": f"Error code '{code}' not documented",
                "available_codes": list(ERROR_CATALOG.keys()),
                "summary": f"error code '{code}' not found",
            }
        return {
            "found": True,
            "code": err_doc.code.value,
            "category": err_doc.category,
            "description": err_doc.description,
            "causes": err_doc.causes,
            "remediation": err_doc.remediation,
            "summary": f"{err_doc.code.value}: {err_doc.description}",
        }

    return {"error": f"unknown action: {action}", "summary": "error: unknown action"}


# Tool Registration


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register introspection tools with FastMCP server."""

    @mcp.tool(
        annotations={
            "title": "Describe: tool params and error codes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def describe(
        ctx: Context,
        action: Literal["tool", "error"] = Field(
            ..., description="Introspection action: 'tool' or 'error'"
        ),
        name: str | None = Field(
            None, description="Tool name to describe (required for action='tool')"
        ),
        code: str | None = Field(
            None, description="Error code to describe (required for action='error')"
        ),
    ) -> dict[str, Any]:
        """Introspection: describe tool parameters or error codes."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        from coderecon.mcp._compat import get_tools_sync

        td = get_tools_sync(mcp)
        return describe_core(
            action=action,
            name=name,
            code=code,
            available_tool_names=list(td.keys()),
            tools_dict=td,
        )
