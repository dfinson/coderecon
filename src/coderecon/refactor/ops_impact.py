"""Impact-analysis mixin for RefactorOps."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

import structlog

if TYPE_CHECKING:
    pass

from coderecon.refactor.ops_models import EditHunk, FileEdit, RefactorPreview, RefactorResult, _word_boundary_match

log = structlog.get_logger(__name__)


class _ImpactMixin:
    """Mixin providing impact-analysis methods for RefactorOps."""

    async def impact(
        self,
        target: str,
        *,
        include_comments: bool = True,
    ) -> RefactorResult:
        """Find all references to a symbol or file for impact analysis.
        Unlike rename/move, impact doesn't auto-fix references - it surfaces them
        for manual cleanup since removal semantics vary (remove import, replace
        with alternative, etc.).
        Args:
            target: Symbol name or file path to analyze
            include_comments: Include comment references in preview
        Returns:
            RefactorResult with preview showing all references.
            Hunks have old=target, new="" to indicate affected sites.
        """
        refactor_id = str(uuid.uuid4())[:8]
        edits_by_file: dict[str, list[EditHunk]] = {}
        seen_locations: set[tuple[str, int]] = set()
        # Check if target is a file path or symbol name
        is_file = "/" in target or target.endswith(".py")
        if is_file:
            # Find imports of this file/module
            await self._find_file_references(target, seen_locations, edits_by_file)
        else:
            # Find references to this symbol
            await self._find_symbol_references(target, seen_locations, edits_by_file)
        # Lexical fallback for both cases
        await self._add_impact_lexical_fallback(target, seen_locations, edits_by_file)
        # Scan comments if requested
        if include_comments:
            affected_files = set(edits_by_file.keys())
            # For impact analysis, mark comment refs for review
            await self._add_comment_occurrences(target, "", affected_files, edits_by_file)
        # Build preview with guidance
        preview = self._build_impact_preview(target, edits_by_file)
        self._pending[refactor_id] = preview
        return RefactorResult(
            refactor_id=refactor_id,
            status="previewed",
            preview=preview,
        )
    async def _find_file_references(
        self,
        file_path: str,
        seen_locations: set[tuple[str, int]],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Find all imports referencing a file/module.
        Supports all languages via language-aware import variant generation.
        """
        from sqlmodel import select
        from coderecon.index._internal.indexing.module_mapping import (
            file_to_import_candidates,
            file_to_import_sql_patterns,
        )
        from coderecon.index.models import File, ImportFact
        # Normalize path
        file_path = file_path.lstrip("./").rstrip("/")
        # Look up source file to get language_family and declared_module
        language_family: str | None = None
        declared_module: str | None = None
        with self._coordinator.db.session() as session:
            file_record = session.exec(select(File).where(File.path == file_path)).first()
            if file_record:
                language_family = file_record.language_family
                declared_module = file_record.declared_module
        # Generate import candidates using language-aware utility
        candidates = file_to_import_candidates(file_path, language_family, declared_module)
        exact_matches, prefix_patterns = file_to_import_sql_patterns(
            file_path, language_family, declared_module
        )
        # Also match bare name for Python "import foo" style
        bare_names: list[str] = []
        for candidate in candidates:
            bare = candidate.split(".")[-1] if "." in candidate else candidate
            if bare and bare not in bare_names:
                bare_names.append(bare)
        with self._coordinator.db.session() as session:
            from sqlmodel import col, or_, select
            # Build SQL conditions for all variants
            conditions: list[Any] = []
            # Exact matches
            if exact_matches:
                conditions.append(col(ImportFact.source_literal).in_(exact_matches))
            # Prefix matches (submodule imports)
            for prefix in prefix_patterns:
                conditions.append(col(ImportFact.source_literal).like(f"{prefix}%"))
            # Bare name matches for imported_name
            if bare_names:
                conditions.append(col(ImportFact.imported_name).in_(bare_names))
            # Also check imported_name for full module paths
            if candidates:
                conditions.append(col(ImportFact.imported_name).in_(candidates))
            if not conditions:
                return
            stmt = (
                select(ImportFact, File.path)
                .join(File, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                .where(or_(*conditions))
            )
            results = session.exec(stmt).all()
            for imp, ref_file in results:
                full_path = self._repo_root / ref_file
                if full_path.exists():
                    try:
                        content = full_path.read_text(encoding="utf-8")
                        lines = content.splitlines()
                        # Find the import line
                        search_term = imp.source_literal or imp.imported_name
                        for i, line in enumerate(lines, 1):
                            if search_term in line and "import" in line.lower():
                                loc = (ref_file, i)
                                if loc not in seen_locations:
                                    seen_locations.add(loc)
                                    # source_literal match = high certainty
                                    # imported_name only = medium certainty
                                    cert: Literal["high", "medium", "low"] = (
                                        "high" if imp.source_literal else "medium"
                                    )
                                    edits_by_file.setdefault(ref_file, []).append(
                                        EditHunk(
                                            old=line.strip(),
                                            new="",  # Deletion marker
                                            line=i,
                                            certainty=cert,
                                        )
                                    )
                                break
                    except (OSError, UnicodeDecodeError):
                        log.debug("delete_import_scan_skip", exc_info=True)
    async def _find_symbol_references(
        self,
        symbol: str,
        seen_locations: set[tuple[str, int]],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Find all references to a symbol."""
        # Get all definitions with this name
        all_defs = await self._coordinator.get_all_defs(symbol)
        for def_fact in all_defs:
            # Mark the definition site
            def_file = await self._get_file_path(def_fact.file_id)
            if def_file:
                loc = (def_file, def_fact.start_line)
                if loc not in seen_locations:
                    seen_locations.add(loc)
                    edits_by_file.setdefault(def_file, []).append(
                        EditHunk(
                            old=def_fact.name,
                            new="",  # Deletion marker
                            line=def_fact.start_line,
                            certainty="high",
                        )
                    )
            # Get all references
            refs = await self._coordinator.get_all_references(def_fact, _context_id=0)
            for ref in refs:
                ref_file = await self._get_file_path(ref.file_id)
                if ref_file:
                    loc = (ref_file, ref.start_line)
                    if loc not in seen_locations:
                        seen_locations.add(loc)
                        cert: Literal["high", "medium", "low"] = (
                            "high" if ref.certainty == "CERTAIN" else "low"
                        )
                        edits_by_file.setdefault(ref_file, []).append(
                            EditHunk(
                                old=symbol,
                                new="",
                                line=ref.start_line,
                                certainty=cert,
                            )
                        )
    async def _add_impact_lexical_fallback(
        self,
        target: str,
        seen_locations: set[tuple[str, int]],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Use Tantivy index for delete lexical fallback - NOT filesystem scan.
        Searches for all import variants via the index.
        """
        from sqlmodel import select
        from coderecon.index._internal.indexing.module_mapping import file_to_import_candidates
        from coderecon.index.models import File
        # Build search patterns based on target type
        patterns: list[str] = [target]
        # If target looks like a file path, generate all import variants
        if "/" in target or target.endswith(".py"):
            # Look up file info for language-aware variant generation
            language_family: str | None = None
            declared_module: str | None = None
            with self._coordinator.db.session() as session:
                file_record = session.exec(
                    select(File).where(File.path == target.lstrip("./").rstrip("/"))
                ).first()
                if file_record:
                    language_family = file_record.language_family
                    declared_module = file_record.declared_module
            # Add all import variants
            for candidate in file_to_import_candidates(target, language_family, declared_module):
                if candidate not in patterns:
                    patterns.append(candidate)
        for pattern in patterns:
            search_response = await self._coordinator.search(pattern, limit=500)
            for hit in search_response.results:
                loc = (hit.path, hit.line)
                if loc in seen_locations:
                    continue
                # Verify word boundary match
                if hit.snippet and _word_boundary_match(hit.snippet, pattern):
                    seen_locations.add(loc)
                    edits_by_file.setdefault(hit.path, []).append(
                        EditHunk(
                            old=pattern,
                            new="",
                            line=hit.line,
                            certainty="low",
                        )
                    )
    def _build_impact_preview(
        self,
        target: str,
        edits_by_file: dict[str, list[EditHunk]],
    ) -> RefactorPreview:
        """Build preview with impact-analysis-specific guidance."""
        preview = self._build_preview(edits_by_file)
        # Override guidance for impact operation
        total_refs = sum(len(fe.hunks) for fe in preview.edits)
        preview.verification_required = True
        preview.verification_guidance = (
            f"Found {total_refs} references to '{target}' that need cleanup.\n\n"
            f"Impact does NOT auto-remove references. You must:\n"
            f"  1. Review each reference with refactor_commit(refactor_id, inspect_path=...)\n"
            f"  2. Decide how to handle: remove import, replace with alternative, etc.\n"
            f"  3. Use refactor_edit to make changes manually\n"
            f"  4. Call refactor_cancel to clear this preview\n\n"
            f"High certainty: {preview.high_certainty_count} (index-backed)\n"
            f"Low certainty: {preview.low_certainty_count} (lexical matches)"
        )
        return preview
