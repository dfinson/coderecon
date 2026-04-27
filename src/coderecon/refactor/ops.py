"""Refactor operations — rename, move, inspect, impact analysis, apply."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

if TYPE_CHECKING:
    from coderecon.index.models import RefFact
    from coderecon.index.ops import IndexCoordinatorEngine

from coderecon.refactor.ops_models import (
    EditHunk,
    FileEdit,
    InspectResult,
    RefactorPreview,
    RefactorResult,
    _compute_rename_certainty_from_ref,
    _scan_file_for_comment_occurrences,
    _word_boundary_match,
)
from coderecon.refactor.ops_move import _MoveMixin
from coderecon.refactor.ops_impact import _ImpactMixin
from coderecon.refactor.ops_apply import _ApplyMixin

log = structlog.get_logger(__name__)

class RefactorOps(_MoveMixin, _ImpactMixin, _ApplyMixin):
    """Refactoring via index-based candidate discovery.
    Uses DefFact/RefFact to find rename candidates with certainty scores.
    Agent reviews low-certainty candidates before applying.
    """
    def __init__(
        self,
        repo_root: Path,
        coordinator: IndexCoordinatorEngine,
    ) -> None:
        """Initialize refactor ops.
        Args:
            repo_root: Repository root path
            coordinator: IndexCoordinatorEngine for symbol lookup
        """
        self._repo_root = repo_root
        self._coordinator = coordinator
        self._pending: dict[str, RefactorPreview] = {}
    def _compute_rename_certainty(self, ref: RefFact) -> Literal["high", "medium", "low"]:
        """Compute certainty for a rename candidate.
        Delegates to module-level function for reusability.
        """
        return _compute_rename_certainty_from_ref(ref)
    async def _extract_symbol_at_location(
        self,
        file_path: str,
        line_num: int,
    ) -> str | None:
        """Try to extract symbol name from a file:line location.
        Used when agent mistakenly passes path:line:col format.
        Returns the symbol name if found at that line.
        """
        full_path = self._repo_root / file_path
        if not full_path.exists():
            return None
        # Read the line and try to extract identifier
        try:
            content = full_path.read_text()
            lines = content.splitlines()
            if 0 < line_num <= len(lines):
                line = lines[line_num - 1]
                # Try to find a likely symbol name (class/def/var assignment)
                patterns = [
                    r"class\s+(\w+)",
                    r"def\s+(\w+)",
                    r"async\s+def\s+(\w+)",
                    r"^(\w+)\s*=",
                    r"^\s*(\w+)\s*:",
                ]
                for pattern in patterns:
                    m = re.search(pattern, line)
                    if m:
                        return m.group(1)
        except (OSError, UnicodeDecodeError):
            log.debug("symbol_at_line_read_failed", file=file_path, exc_info=True)
        return None
    async def rename(
        self,
        symbol: str,
        new_name: str,
        *,
        _include_comments: bool = True,
        _contexts: list[str] | None = None,
    ) -> RefactorResult:
        """Rename a symbol across the codebase.
        Uses index to find ALL definitions with the given name and their
        references. Also performs codebase-wide lexical search as fallback.
        Returns candidates with certainty scores for agent review.
        Args:
            symbol: Symbol name (e.g., "MyClass", "my_function")
            new_name: New name for the symbol
            _include_comments: Also update comments/docs (default True)
            _contexts: Limit to specific contexts
        Returns:
            RefactorResult with preview. Call apply() to execute.
        """
        # Detect and strip path:line:col prefix if agent mistakenly passed it
        symbol_warning: str | None = None
        path_line_col_pattern = re.compile(r"^(.+):(\d+):(\d+)$")
        match = path_line_col_pattern.match(symbol)
        if match:
            # Extract just the symbol name - this is a guess, warn the agent
            original_input = symbol
            # The "symbol" they probably meant is just the identifier, not the locator
            # We can't reliably extract it from path:line:col, so search the file
            file_path = match.group(1)
            line_num = int(match.group(2))
            # Try to find definition at that location to get actual symbol name
            extracted_symbol = await self._extract_symbol_at_location(file_path, line_num)
            if extracted_symbol:
                symbol = extracted_symbol
                symbol_warning = (
                    f"WARNING: Detected path:line:col format '{original_input}'. "
                    f"Extracted symbol name '{symbol}'. "
                    f"In future, pass just the symbol name directly (e.g., 'MyClass' not 'path/file.py:10:5'). "
                    f"Please verify '{symbol}' is the correct symbol to rename."
                )
            else:
                # Couldn't extract - return early with error guidance
                return RefactorResult(
                    refactor_id=str(uuid.uuid4())[:8],
                    status="previewed",
                    preview=RefactorPreview(
                        files_affected=0,
                        edits=[],
                        verification_required=True,
                        verification_guidance=(
                            f"ERROR: Received path:line:col format '{original_input}' but could not "
                            f"extract symbol name at that location. "
                            f"Pass the symbol name directly (e.g., 'MyClass' not 'path/file.py:10:5')."
                        ),
                    ),
                )
        refactor_id = str(uuid.uuid4())[:8]
        # Find ALL definitions with this name (not just the first)
        all_defs = await self._coordinator.get_all_defs(symbol)
        edits_by_file: dict[str, list[EditHunk]] = {}
        seen_locations: set[tuple[str, int]] = set()  # (path, line) to dedupe
        # Process each definition and its references
        for def_fact in all_defs:
            def_file = await self._get_file_path(def_fact.file_id)
            if def_file:
                loc = (def_file, def_fact.start_line)
                if loc not in seen_locations:
                    seen_locations.add(loc)
                    edits_by_file.setdefault(def_file, []).append(
                        EditHunk(
                            old=def_fact.name,
                            new=new_name,
                            line=def_fact.start_line,
                            certainty="high",
                        )
                    )
            # Get references for this definition
            refs = await self._coordinator.get_all_references(def_fact, _context_id=0)
            for ref in refs:
                ref_file = await self._get_file_path(ref.file_id)
                if ref_file:
                    loc = (ref_file, ref.start_line)
                    if loc not in seen_locations:
                        seen_locations.add(loc)
                        # Compute certainty based on RefTier (per SPEC.md)
                        cert = self._compute_rename_certainty(ref)
                        edits_by_file.setdefault(ref_file, []).append(
                            EditHunk(
                                old=symbol,
                                new=new_name,
                                line=ref.start_line,
                                certainty=cert,
                            )
                        )
        # Codebase-wide lexical search as low-certainty fallback
        await self._add_lexical_fallback(symbol, new_name, seen_locations, edits_by_file)
        # Scan for comment/docstring occurrences
        if _include_comments:
            affected_files = set(edits_by_file.keys())
            await self._add_comment_occurrences(symbol, new_name, affected_files, edits_by_file)
        # Build preview
        file_edits = [FileEdit(path=path, hunks=hunks) for path, hunks in edits_by_file.items()]
        high_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "high")
        medium_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "medium")
        low_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "low")
        # Build compact verification guidance if there are low-certainty matches
        verification_required = low_count > 0
        low_certainty_files: list[str] = []
        verification_guidance = None
        if verification_required:
            # Collect files with low-certainty matches and their counts
            file_counts: dict[str, int] = {}
            for fe in file_edits:
                low_in_file = sum(1 for h in fe.hunks if h.certainty == "low")
                if low_in_file > 0:
                    file_counts[fe.path] = low_in_file
                    low_certainty_files.append(fe.path)
            files_summary = ", ".join(f"{p} ({c})" for p, c in list(file_counts.items())[:5])
            if len(file_counts) > 5:
                files_summary += f", ... and {len(file_counts) - 5} more files"
            verification_guidance = (
                f"{low_count} low-certainty lexical matches may include false positives "
                f"(e.g., English word vs symbol name).\n\n"
                f"Files: {files_summary}\n\n"
                f"BEFORE calling refactor_commit:\n"
                f"  1. Use refactor_commit(refactor_id, inspect_path=path) to review matches with context\n"
                f"  2. Or read files to verify manually\n"
                f"  3. If false positives exist, use refactor_cancel and handle manually"
            )
        preview = RefactorPreview(
            files_affected=len(file_edits),
            edits=file_edits,
            high_certainty_count=high_count,
            medium_certainty_count=medium_count,
            low_certainty_count=low_count,
            verification_required=verification_required,
            low_certainty_files=low_certainty_files,
            verification_guidance=verification_guidance,
        )
        self._pending[refactor_id] = preview
        return RefactorResult(
            refactor_id=refactor_id,
            status="previewed",
            preview=preview,
            warning=symbol_warning,
        )
    async def _add_comment_occurrences(
        self,
        symbol: str,
        new_name: str,
        affected_files: set[str],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Scan affected files for comment/docstring occurrences."""
        from coderecon.index.models import File
        for file_path in affected_files:
            full_path = self._repo_root / file_path
            if not full_path.exists():
                continue
            try:
                content = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                log.debug("refactor_comment_scan_skip", path=file_path, exc_info=True)
                continue
            # Detect language from file
            with self._coordinator.db.session() as session:
                from sqlmodel import select
                file_record = session.exec(select(File).where(File.path == file_path)).first()
                language = file_record.language_family if file_record else None
            # Find comment occurrences
            comment_hits = _scan_file_for_comment_occurrences(content, symbol, language)
            # Get existing edit lines to avoid duplicates
            existing_lines = {h.line for h in edits_by_file.get(file_path, [])}
            for line_num, _snippet in comment_hits:
                if line_num not in existing_lines:
                    edits_by_file.setdefault(file_path, []).append(
                        EditHunk(
                            old=symbol,
                            new=new_name,
                            line=line_num,
                            certainty="medium",  # Comment occurrences are medium certainty
                        )
                    )
    async def _add_lexical_fallback(
        self,
        symbol: str,
        new_name: str,
        seen_locations: set[tuple[str, int]],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Use Tantivy index for lexical fallback - NOT filesystem scan.
        Queries the index for the symbol, then scans matched files for ALL
        occurrences. Tantivy returns one result per document (file), so we
        need to find all line occurrences within each matched file.
        """
        # Search the index for the symbol
        search_response = await self._coordinator.search(symbol, limit=500)
        # Collect unique file paths from search results
        matched_files: set[str] = set()
        for hit in search_response.results:
            if hit.snippet and _word_boundary_match(hit.snippet, symbol):
                matched_files.add(hit.path)
        # For each matched file, scan for ALL occurrences
        for file_path in matched_files:
            full_path = self._repo_root / file_path
            if not full_path.exists():
                continue
            try:
                content = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                log.debug("refactor_lexical_scan_skip", path=file_path, exc_info=True)
                continue
            # Find all lines containing the symbol with word boundaries
            lines = content.splitlines()
            for line_num, line in enumerate(lines, 1):  # 1-indexed
                if _word_boundary_match(line, symbol):
                    loc = (file_path, line_num)
                    if loc not in seen_locations:
                        seen_locations.add(loc)
                        edits_by_file.setdefault(file_path, []).append(
                            EditHunk(
                                old=symbol,
                                new=new_name,
                                line=line_num,
                                certainty="low",
                            )
                        )
    async def inspect(
        self,
        refactor_id: str,
        path: str,
        *,
        context_lines: int = 2,
    ) -> InspectResult:
        """Inspect low-certainty matches in a file with surrounding context.
        Use this to verify lexical matches before applying a refactor.
        Args:
            refactor_id: ID from refactor_rename preview
            path: File path to inspect
            context_lines: Lines of context before/after (default 2)
        Returns:
            InspectResult with snippets and context for each match
        """
        preview = self._pending.get(refactor_id)
        if preview is None:
            return InspectResult(path=path, matches=[])
        # Find the file in the preview
        file_edit = next((fe for fe in preview.edits if fe.path == path), None)
        if file_edit is None:
            return InspectResult(path=path, matches=[])
        # Read the file
        full_path = self._repo_root / path
        try:
            content = full_path.read_text(encoding="utf-8")
            lines = content.splitlines()
        except (OSError, UnicodeDecodeError):
            return InspectResult(path=path, matches=[])
        matches: list[dict[str, str | int]] = []
        for hunk in file_edit.hunks:
            if hunk.certainty != "low":
                continue
            line_idx = hunk.line - 1  # 0-indexed
            if 0 <= line_idx < len(lines):
                # Get context
                start = max(0, line_idx - context_lines)
                end = min(len(lines), line_idx + context_lines + 1)
                matches.append(
                    {
                        "line": hunk.line,
                        "snippet": lines[line_idx].strip(),
                        "context_before": "\n".join(lines[start:line_idx]),
                        "context_after": "\n".join(lines[line_idx + 1 : end]),
                    }
                )
        return InspectResult(path=path, matches=matches)
    async def _get_file_path(self, file_id: int) -> str | None:
        """Look up file path from file_id."""
        from coderecon.index.models import File
        with self._coordinator.db.session() as session:
            file = session.get(File, file_id)
            return file.path if file else None

    def _build_preview(self, edits_by_file: dict[str, list[EditHunk]]) -> RefactorPreview:
        """Build RefactorPreview from edits."""
        file_edits = [FileEdit(path=path, hunks=hunks) for path, hunks in edits_by_file.items()]
        high_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "high")
        medium_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "medium")
        low_count = sum(1 for fe in file_edits for h in fe.hunks if h.certainty == "low")
        verification_required = low_count > 0
        low_certainty_files: list[str] = []
        verification_guidance = None
        if verification_required:
            file_counts: dict[str, int] = {}
            for fe in file_edits:
                low_in_file = sum(1 for h in fe.hunks if h.certainty == "low")
                if low_in_file > 0:
                    file_counts[fe.path] = low_in_file
                    low_certainty_files.append(fe.path)
            files_summary = ", ".join(f"{p} ({c})" for p, c in list(file_counts.items())[:5])
            if len(file_counts) > 5:
                files_summary += f", ... and {len(file_counts) - 5} more files"
            verification_guidance = (
                f"{low_count} low-certainty matches found.\n\n"
                f"Files: {files_summary}\n\n"
                f"Use refactor_commit(refactor_id, inspect_path=path) to review before applying."
            )
        return RefactorPreview(
            files_affected=len(file_edits),
            edits=file_edits,
            high_certainty_count=high_count,
            medium_certainty_count=medium_count,
            low_certainty_count=low_count,
            verification_required=verification_required,
            low_certainty_files=low_certainty_files,
            verification_guidance=verification_guidance,
        )
