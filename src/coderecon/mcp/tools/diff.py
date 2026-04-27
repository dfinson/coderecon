"""Layer 4: MCP tool for semantic diff.

Orchestrates the full pipeline: sources -> engine -> enrichment -> output.
"""

# Removed: from __future__ import annotations - breaks FastMCP+pydantic Literal resolution

import contextlib
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

from coderecon.core.languages import detect_language_family, has_grammar
from coderecon.git.models import _DELTA_CHAR_MAP
from coderecon.index._internal.diff.engine import compute_structural_diff
from coderecon.index._internal.diff.enrichment import enrich_diff
from coderecon.index._internal.diff.models import (
    AnalysisScope,
    ChangedFile,
    DefSnapshot,
    SemanticDiffResult,
)
from coderecon.index._internal.diff.sources import (
    snapshots_from_blob,
    snapshots_from_epoch,
    snapshots_from_index,
)
from coderecon.mcp.tools.diff_formatting import _result_to_text

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from coderecon.mcp.context import AppContext

log = structlog.get_logger(__name__)

# Core Function (transport-agnostic)

def semantic_diff_core(
    app_ctx: "AppContext",
    *,
    base: str = "HEAD",
    target: str | None = None,
    paths: list[str] | None = None,
    scope_id: str | None = None,
) -> dict[str, Any]:
    """Structural change summary (transport-agnostic).
    Compares definitions between two states and reports what changed.
    """
    if base.startswith("epoch:"):
        result = _run_epoch_diff(app_ctx, base, target, paths)
    else:
        result = _run_git_diff(app_ctx, base, target, paths)
    from coderecon.mcp.delivery import wrap_response
    return wrap_response(
        _result_to_text(result),
        resource_kind="semantic_diff",
    )

# Tool Registration

def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register semantic_diff MCP tool."""
    @mcp.tool(
        annotations={
            "title": "Diff: structural change summary",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def semantic_diff(
        ctx: Context,
        base: str = Field("HEAD", description="Base ref (commit, branch, tag) or epoch:N"),
        target: str | None = Field(None, description="Target ref (None = working tree)"),
        paths: list[str] | None = Field(None, description="Limit to specific paths"),
        scope_id: str | None = Field(None, description="Scope ID for budget tracking"),
    ) -> dict[str, Any]:
        """Structural change summary from index facts."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)
        return semantic_diff_core(
            app_ctx, base=base, target=target, paths=paths, scope_id=scope_id,
        )

def _run_git_diff(
    app_ctx: "AppContext",
    base: str,
    target: str | None,
    paths: list[str] | None,
) -> SemanticDiffResult:
    """Run semantic diff in git mode."""
    import re

    from coderecon.git._internal.planners import DiffPlanner
    git_ops = app_ctx.git_ops
    planner = DiffPlanner(git_ops._access)
    plan = planner.plan(base=base, target=target, staged=False)
    diff_result = planner.execute(plan)
    # Extract changed files from numstat
    changed_files: list[ChangedFile] = []
    hunks: dict[str, list[tuple[int, int]]] = {}
    for status_char, _adds, _dels, file_path in diff_result.numstat:
        if paths and file_path not in paths:
            continue
        status = _DELTA_CHAR_MAP.get(status_char, "modified")
        lang = detect_language_family(file_path)
        file_has_grammar = bool(lang and has_grammar(lang))
        changed_files.append(ChangedFile(file_path, status, file_has_grammar, language=lang))
    # Extract hunks from unified diff text
    _HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
    current_file: str | None = None
    for line in diff_result.diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in hunks:
                hunks[current_file] = []
        elif line.startswith("+++ /dev/null"):
            current_file = None
        elif current_file:
            m = _HUNK_RE.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                end = start + count - 1
                if end >= start:
                    hunks[current_file].append((start, end))
    # Build snapshots for each file
    base_facts: dict[str, list[DefSnapshot]] = {}
    target_facts: dict[str, list[DefSnapshot]] = {}
    # Resolve base ref for blob parsing
    base_sha = plan.base_sha
    coordinator = app_ctx.coordinator
    db = coordinator.db
    # Single session for all index lookups + enrichment
    with db.session() as session:
        for cf in changed_files:
            if not cf.has_grammar:
                continue
            # Target: current index state
            target_facts[cf.path] = snapshots_from_index(session, cf.path)
            # Base: parse from git blob (CPU, no DB)
            if base_sha and cf.status != "added":
                base_facts[cf.path] = snapshots_from_blob(git_ops._access, base_sha, cf.path)
            else:
                base_facts[cf.path] = []
        # Run engine
        raw = compute_structural_diff(base_facts, target_facts, changed_files, hunks)
        # Enrich (reuse same session)
        result = enrich_diff(raw, session, app_ctx.repo_root)
    # Annotate with change previews from the actual patch lines
    _annotate_change_previews(result, diff_result.diff_text)
    result.base_description = base or "HEAD"
    result.target_description = target or "working tree"
    # Build analysis scope
    files_parsed = len([cf for cf in changed_files if cf.has_grammar])
    files_no_grammar = len([cf for cf in changed_files if not cf.has_grammar])
    languages = sorted({cf.language for cf in changed_files if cf.language})
    # Detect worktree dirty state (target is worktree when target param is None)
    worktree_dirty: bool | None = None
    if target is None:
        with contextlib.suppress(Exception):
            worktree_dirty = git_ops.status() != {}
    result.scope = AnalysisScope(
        base_sha=plan.base_sha,
        target_sha=plan.target_sha,
        worktree_dirty=worktree_dirty,
        mode="git",
        entity_id_scheme="def_uid_v1",
        files_parsed=files_parsed,
        files_no_grammar=files_no_grammar,
        languages_analyzed=languages,
    )
    return result

def _parse_epoch_ref(ref: str) -> int:
    """Parse an epoch reference like 'epoch:3' into a non-negative integer.
    Only non-negative numeric epoch IDs are supported (e.g. epoch:0, epoch:1, epoch:42).
    Named aliases like 'epoch:previous' are not implemented.
    Raises:
        ValueError: If the epoch value is not a valid non-negative integer.
    """
    parts = ref.split(":", 1)
    if len(parts) != 2 or parts[0] != "epoch":
        msg = f"Invalid epoch reference: {ref!r}. Expected format: epoch:<int>"
        raise ValueError(msg)
    try:
        value = int(parts[1])
    except ValueError:
        msg = (
            f"Invalid epoch value: {parts[1]!r}. "
            f"Only numeric epoch IDs are supported (e.g. epoch:1, epoch:42)."
        )
        raise ValueError(msg) from None
    if value < 0:
        msg = f"Epoch ID must be non-negative, got {value}."
        raise ValueError(msg)
    return value

def _run_epoch_diff(
    app_ctx: "AppContext",
    base: str,
    target: str | None,
    paths: list[str] | None,
) -> SemanticDiffResult:
    """Run semantic diff in epoch mode."""
    from coderecon.index.models import DefSnapshotRecord
    base_epoch = _parse_epoch_ref(base)
    target_epoch = _parse_epoch_ref(target) if target and target.startswith("epoch:") else None
    coordinator = app_ctx.coordinator
    db = coordinator.db
    with db.session() as session:
        from sqlmodel import select
        # Reconstruct file state at each epoch by finding all files
        # that have any snapshot at or before the epoch
        base_files_stmt = (
            select(DefSnapshotRecord.file_path)
            .where(DefSnapshotRecord.epoch_id <= base_epoch)
            .distinct()
        )
        base_file_paths = set(session.exec(base_files_stmt).all())
        target_file_paths: set[str] = set()
        if target_epoch is not None:
            target_files_stmt = (
                select(DefSnapshotRecord.file_path)
                .where(DefSnapshotRecord.epoch_id <= target_epoch)
                .distinct()
            )
            target_file_paths = set(session.exec(target_files_stmt).all())
        all_paths = base_file_paths | target_file_paths
        if paths:
            all_paths = all_paths & set(paths)
        # Build changed files and facts
        changed_files: list[ChangedFile] = []
        base_facts: dict[str, list[DefSnapshot]] = {}
        target_facts: dict[str, list[DefSnapshot]] = {}
        for fp in sorted(all_paths):
            lang = detect_language_family(fp)
            file_has_grammar = bool(lang and has_grammar(lang))
            # Reconstruct per-epoch state via snapshots_from_epoch
            # (which uses epoch_id <= target to get full state)
            base_snaps = snapshots_from_epoch(session, base_epoch, fp)
            if target_epoch is not None:
                target_snaps = snapshots_from_epoch(session, target_epoch, fp)
            else:
                target_snaps = snapshots_from_index(session, fp)
            base_exists = bool(base_snaps)
            target_exists = bool(target_snaps)
            if base_exists and not target_exists:
                status = "deleted"
            elif not base_exists and target_exists:
                status = "added"
            else:
                status = "modified"
            changed_files.append(ChangedFile(fp, status, file_has_grammar, language=lang))
            base_facts[fp] = base_snaps
            target_facts[fp] = target_snaps
        # Run engine (no hunks in epoch mode)
        raw = compute_structural_diff(base_facts, target_facts, changed_files, hunks=None)
        # Enrich (reuse same session)
        result = enrich_diff(raw, session, app_ctx.repo_root)
    result.base_description = f"epoch {base_epoch}"
    result.target_description = f"epoch {target_epoch}" if target_epoch else "current index"
    # Build analysis scope for epoch mode
    files_parsed = len([cf for cf in changed_files if cf.has_grammar])
    files_no_grammar = len([cf for cf in changed_files if not cf.has_grammar])
    languages = sorted({cf.language for cf in changed_files if cf.language})
    result.scope = AnalysisScope(
        base_sha=None,
        target_sha=None,
        worktree_dirty=None,
        mode="epoch",
        entity_id_scheme="def_uid_v1",
        files_parsed=files_parsed,
        files_no_grammar=files_no_grammar,
        languages_analyzed=languages,
    )
    return result

_PREVIEW_MAX_LINES = 5  # Max changed lines to include in preview

def _extract_patch_lines(
    diff_text: str,
) -> dict[str, list[tuple[str, int, str]]]:
    """Extract per-file patch lines from unified diff text.
    Returns a dict mapping file_path -> list of (origin, line_number, content)
    where origin is '+' for additions, '-' for deletions.
    """
    import re
    result: dict[str, list[tuple[str, int, str]]] = {}
    current_file: str | None = None
    hunk_re = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    old_lineno = 0
    new_lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in result:
                result[current_file] = []
        elif line.startswith("+++ /dev/null"):
            current_file = None
        elif current_file:
            m = hunk_re.match(line)
            if m:
                old_lineno = int(m.group(1))
                new_lineno = int(m.group(2))
            elif line.startswith("+"):
                result[current_file].append(("+", new_lineno, line[1:]))
                new_lineno += 1
            elif line.startswith("-"):
                result[current_file].append(("-", old_lineno, line[1:]))
                old_lineno += 1
            elif line.startswith(" "):
                old_lineno += 1
                new_lineno += 1
    return result

def _annotate_change_previews(
    result: SemanticDiffResult,
    diff_text: str,
) -> None:
    """Annotate structural changes with a text preview of what changed.
    Only applies to body_changed and signature_changed entries in git mode.
    Patches each StructuralChange.change_preview in place.
    """
    try:
        patch_lines = _extract_patch_lines(diff_text)
    except (ValueError, IndexError):
        log.debug("patch_line_extraction_failed", exc_info=True)
        return
    for change in result.structural_changes:
        if change.change not in ("body_changed", "signature_changed"):
            continue
        file_lines = patch_lines.get(change.path)
        if not file_lines:
            continue
        # Filter lines within this entity's span
        relevant: list[str] = []
        for origin, lineno, content in file_lines:
            if change.start_line <= lineno <= change.end_line:
                relevant.append(f"{origin} {content}")
                if len(relevant) >= _PREVIEW_MAX_LINES:
                    break
        if relevant:
            change.change_preview = "\n".join(relevant)
        # Also annotate nested changes
        if change.nested_changes:
            for nc in change.nested_changes:
                if nc.change not in ("body_changed", "signature_changed"):
                    continue
                nc_lines = patch_lines.get(nc.path)
                if not nc_lines:
                    continue
                nc_relevant: list[str] = []
                for origin, lineno, content in nc_lines:
                    if nc.start_line <= lineno <= nc.end_line:
                        nc_relevant.append(f"{origin} {content}")
                        if len(nc_relevant) >= _PREVIEW_MAX_LINES:
                            break
                if nc_relevant:
                    nc.change_preview = "\n".join(nc_relevant)

