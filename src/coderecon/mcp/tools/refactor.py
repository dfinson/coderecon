"""Refactor MCP tools — semantic rename, move, impact analysis."""

from typing import TYPE_CHECKING, Any

from fastmcp import Context
from pydantic import Field

from coderecon.mcp.errors import MCPError, MCPErrorCode

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from coderecon.mcp.context import AppContext
    from coderecon.refactor.ops import RefactorResult


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_refactor(status: str, files_affected: int, preview: Any) -> str:
    """Generate summary for refactor operations."""
    if status == "cancelled":
        return "refactoring cancelled"
    if status == "applied":
        return f"applied to {files_affected} files"
    if status in ("pending", "previewed") and preview:
        high = preview.high_certainty_count or 0
        med = preview.medium_certainty_count or 0
        low = preview.low_certainty_count or 0
        total = high + med + low
        parts = [f"preview: {total} changes in {files_affected} files"]
        if low:
            parts.append(f"({low} need review)")
        return " ".join(parts)
    return status


def _display_refactor(status: str, files_affected: int, preview: Any, refactor_id: str) -> str:
    """Human-friendly message for refactor operations."""
    if status == "cancelled":
        return "Refactoring cancelled."
    if status == "applied":
        return f"Refactoring applied: {files_affected} files modified."
    if status in ("pending", "previewed") and preview:
        high = preview.high_certainty_count or 0
        low = preview.low_certainty_count or 0
        total = high + (preview.medium_certainty_count or 0) + low
        if low > 0:
            return f"Preview ready: {total} changes in {files_affected} files ({low} require review). Refactor ID: {refactor_id}"
        return (
            f"Preview ready: {total} changes in {files_affected} files. Refactor ID: {refactor_id}"
        )
    return f"Refactoring {status}."


def _build_refactor_agentic_hint(result: "RefactorResult", files_affected: int) -> str:
    """Build next-step instruction for the agent after a refactor operation."""
    rid = result.refactor_id

    if result.status == "cancelled":
        return "Refactoring cancelled. No further action needed."

    if result.status == "applied":
        changed = []
        if result.applied:
            changed = [fd.path for fd in result.applied.files]
        files_str = ", ".join(changed[:5]) if changed else f"{files_affected} file(s)"
        if len(changed) > 5:
            files_str += f" (+{len(changed) - 5} more)"
        return (
            f"Refactoring applied to {files_str}.\n"
            f"NEXT: call checkpoint(changed_files=[{', '.join(repr(p) for p in changed[:5])}], "
            'commit_message="...") to lint, test, and commit.\n'
            "Ask the user whether they want push=True or push=False."
        )

    if result.status in ("pending", "previewed") and result.preview:
        low = result.preview.low_certainty_count or 0
        parts = [f"Preview ready: {files_affected} file(s). Refactor ID: {rid}\n"]
        if low > 0:
            low_files = sorted(
                {fe.path for fe in result.preview.edits for h in fe.hunks if h.certainty == "low"}
            )
            parts.append(
                f"{low} low-certainty match(es) require review.\n"
                f'INSPECT: refactor_commit(refactor_id="{rid}", '
                f'inspect_path="{low_files[0] if low_files else "<path>"}")\n'
            )
        parts.append(
            f'APPLY: refactor_commit(refactor_id="{rid}")\n'
            f'CANCEL: refactor_cancel(refactor_id="{rid}")'
        )
        return "".join(parts)

    return f"Refactoring status: {result.status}."


def _serialize_refactor_result(result: "RefactorResult") -> dict[str, Any]:
    """Convert RefactorResult to dict."""
    # Get files_affected from preview or applied delta
    if result.preview:
        files_affected = result.preview.files_affected
    elif result.applied:
        files_affected = result.applied.files_changed
    else:
        files_affected = 0

    output: dict[str, Any] = {
        "refactor_id": result.refactor_id,
        "status": result.status,
        "summary": _summarize_refactor(result.status, files_affected, result.preview),
        "display_to_user": _display_refactor(
            result.status, files_affected, result.preview, result.refactor_id
        ),
    }

    if result.preview:
        preview_dict: dict[str, Any] = {
            "files_affected": result.preview.files_affected,
            "high_certainty_count": result.preview.high_certainty_count,
            "medium_certainty_count": result.preview.medium_certainty_count,
            "low_certainty_count": result.preview.low_certainty_count,
            "edits": [
                {
                    "path": fe.path,
                    "hunks": [
                        {
                            "old": h.old,
                            "new": h.new,
                            "line": h.line,
                            "certainty": h.certainty,
                        }
                        for h in fe.hunks
                    ],
                }
                for fe in result.preview.edits
            ],
        }
        # Add verification fields if present
        if result.preview.verification_required:
            preview_dict["verification_required"] = True
            # Convert low_certainty_files to low_certainty_matches with span info
            low_matches = []
            for fe in result.preview.edits:
                for h in fe.hunks:
                    if h.certainty == "low":
                        # Compute end_line from old content line count
                        old_lines = h.old.count("\n") + 1 if h.old else 1
                        low_matches.append(
                            {
                                "path": fe.path,
                                "span": {"start_line": h.line, "end_line": h.line + old_lines - 1},
                                "certainty": h.certainty,
                                "match_text": h.old[:80] if h.old else "",
                            }
                        )
            preview_dict["verification_guidance"] = result.preview.verification_guidance
            if low_matches:
                preview_dict["low_certainty_matches"] = low_matches
        output["preview"] = preview_dict

    if result.divergence:
        output["divergence"] = {
            "conflicting_hunks": result.divergence.conflicting_hunks,
            "resolution_options": result.divergence.resolution_options,
        }
    # Include warning if present (e.g., path:line:col format detected)
    if result.warning:
        output["warning"] = result.warning

    # ── Agentic hint — next-step instruction ──
    output["agentic_hint"] = _build_refactor_agentic_hint(result, files_affected)

    from coderecon.mcp.delivery import wrap_response

    return wrap_response(output, resource_kind="refactor_preview")


def _serialize_impact_result(result: "RefactorResult") -> dict[str, Any]:
    """Convert impact RefactorResult to a read-only reference list.

    Unlike _serialize_refactor_result, this does NOT include a refactor_id
    or suggest refactor_commit/cancel — impact analysis is read-only.
    """
    references: list[dict[str, Any]] = []
    if result.preview:
        for fe in result.preview.edits:
            for h in fe.hunks:
                references.append(
                    {
                        "path": fe.path,
                        "line": h.line,
                        "match_text": h.old[:120] if h.old else "",
                        "certainty": h.certainty,
                    }
                )

    files_affected = result.preview.files_affected if result.preview else 0
    from coderecon.mcp.delivery import wrap_response

    return wrap_response(
        {
            "references": references,
            "total_references": len(references),
            "files_affected": files_affected,
            "summary": (f"Found {len(references)} reference(s) across {files_affected} file(s)."),
            "agentic_hint": (
                "Impact analysis complete (read-only). "
                "Use this data to plan your edits. No refactor_commit needed."
            ),
        },
        resource_kind="impact_analysis",
    )


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register refactor tools with FastMCP server."""

    def _require_recon(session: Any) -> None:
        """Gate: recon must have been called before refactoring."""
        if not session.candidate_maps:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message="Recon required before refactoring.",
                remediation=(
                    'Call recon(task="...") first to discover files, then use refactor tools.'
                ),
            )

    @mcp.tool(
        annotations={
            "title": "Rename: cross-file symbol rename",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def refactor_rename(
        ctx: Context,
        symbol: str = Field(
            ...,
            description="Symbol name to rename (e.g., 'MyClass', 'my_function'). Do NOT use path:line:col format.",
        ),
        new_name: str = Field(..., description="New name for the symbol"),
        justification: str = Field(
            ...,
            description="Explain what you are renaming and why.",
        ),
        include_comments: bool = Field(True, description="Include comment references"),
        contexts: list[str] | None = Field(None, description="Limit to specific contexts"),
    ) -> dict[str, Any]:
        """Rename a symbol across the codebase."""
        session = app_ctx.session_manager.get_or_create(ctx.session_id)
        _require_recon(session)

        result = await app_ctx.refactor_ops.rename(
            symbol,
            new_name,
            _include_comments=include_comments,
            _contexts=contexts,
        )
        session.mutation_ctx.pending_refactors[result.refactor_id] = "rename"
        return _serialize_refactor_result(result)

    @mcp.tool(
        annotations={
            "title": "Move: relocate file with import updates",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def refactor_move(
        ctx: Context,
        from_path: str = Field(..., description="Source file path"),
        to_path: str = Field(..., description="Destination file path"),
        justification: str = Field(
            ...,
            description="Explain what you are moving and why.",
        ),
        include_comments: bool = Field(True, description="Include comment references"),
    ) -> dict[str, Any]:
        """Move a file/module, updating imports."""
        session = app_ctx.session_manager.get_or_create(ctx.session_id)
        _require_recon(session)

        result = await app_ctx.refactor_ops.move(
            from_path,
            to_path,
            include_comments=include_comments,
        )
        session.mutation_ctx.pending_refactors[result.refactor_id] = "move"
        return _serialize_refactor_result(result)

    @mcp.tool(
        annotations={
            "title": "Recon: read-only reference analysis",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def recon_impact(
        ctx: Context,
        target: str = Field(..., description="Symbol or path to analyze for impact"),
        justification: str = Field(
            ...,
            description="Explain what you are analyzing and why.",
        ),
        include_comments: bool = Field(True, description="Include comment references"),
    ) -> dict[str, Any]:
        """Find all references to a symbol/file for read-only impact analysis."""
        session = app_ctx.session_manager.get_or_create(ctx.session_id)
        _require_recon(session)

        result = await app_ctx.refactor_ops.impact(
            target,
            include_comments=include_comments,
        )
        return _serialize_impact_result(result)

    @mcp.tool(
        annotations={
            "title": "Commit: apply or inspect refactoring preview",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def refactor_commit(
        ctx: Context,
        refactor_id: str = Field(..., description="ID of the refactoring to apply or inspect"),
        inspect_path: str | None = Field(
            None,
            description=(
                "If provided, inspect low-certainty matches in this file "
                "instead of applying. Returns match details with context."
            ),
        ),
        context_lines: int = Field(
            2,
            description="Lines of context around matches (only used with inspect_path).",
        ),
    ) -> dict[str, Any]:
        """Apply a previewed refactoring, or inspect low-certainty matches.

        Without inspect_path: applies the refactoring.
        With inspect_path: inspects matches in that file.
        """
        session = app_ctx.session_manager.get_or_create(ctx.session_id)

        if inspect_path is not None:
            inspect_result = await app_ctx.refactor_ops.inspect(
                refactor_id,
                inspect_path,
                context_lines=context_lines,
            )
            from coderecon.core.formatting import compress_path

            return {
                "path": inspect_result.path,
                "matches": inspect_result.matches,
                "summary": (
                    f"{len(inspect_result.matches)} matches in "
                    f"{compress_path(inspect_result.path, 35)}"
                ),
            }

        # Apply mode
        async with app_ctx.router.mutation(app_ctx.worktree_name):
            result = await app_ctx.refactor_ops.apply(refactor_id, app_ctx.mutation_ops)
            if result.changed_paths:
                await app_ctx.router.on_mutation(
                    app_ctx.worktree_name, result.changed_paths
                )
        session.mutation_ctx.pending_refactors.pop(refactor_id, None)

        return _serialize_refactor_result(result)

    @mcp.tool(
        annotations={
            "title": "Cancel: discard refactoring preview",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def refactor_cancel(
        ctx: Context,
        refactor_id: str = Field(..., description="ID of the refactoring to cancel"),
    ) -> dict[str, Any]:
        """Cancel a pending refactoring."""
        session = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.refactor_ops.cancel(refactor_id)
        session.mutation_ctx.pending_refactors.pop(refactor_id, None)
        return _serialize_refactor_result(result)
