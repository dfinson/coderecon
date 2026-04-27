"""Move-refactor mixin for RefactorOps."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

import structlog

if TYPE_CHECKING:
    pass

from coderecon.refactor.ops_models import EditHunk, RefactorResult

log = structlog.get_logger(__name__)


class _MoveMixin:
    """Mixin providing move-refactor methods for RefactorOps."""

    async def move(
        self,
        from_path: str,
        to_path: str,
        *,
        include_comments: bool = True,
    ) -> RefactorResult:
        """Move a file/module, updating all imports.
        Uses ImportFact to find all imports referencing the source module
        and generates edits to update them to the new path.
        Supports all languages via language-aware import variant generation:
        - Python/Lua: dotted paths with/without src. prefix
        - Go/Rust/Java/etc.: uses declared_module from File record
        - JS/TS: handled via lexical fallback (relative paths)
        Args:
            from_path: Source path (relative to repo root)
            to_path: Destination path (relative to repo root)
            include_comments: Update comments/docs mentioning the path
        Returns:
            RefactorResult with preview. Call apply() to execute.
        """
        from sqlmodel import select
        from coderecon.index._internal.indexing.module_mapping import (
            file_to_import_candidates,
            file_to_import_sql_patterns,
            get_module_separator,
            infer_target_declared_module,
        )
        from coderecon.index.models import File, ImportFact
        refactor_id = str(uuid.uuid4())[:8]
        edits_by_file: dict[str, list[EditHunk]] = {}
        seen_locations: set[tuple[str, int]] = set()
        # Normalize paths (remove leading ./ and trailing slashes)
        from_path = from_path.lstrip("./").rstrip("/")
        to_path = to_path.lstrip("./").rstrip("/")
        # Look up source file to get language_family and declared_module
        language_family: str | None = None
        declared_module: str | None = None
        with self._coordinator.db.session() as session:
            file_record = session.exec(select(File).where(File.path == from_path)).first()
            if file_record:
                language_family = file_record.language_family
                declared_module = file_record.declared_module
        # Generate import candidates for both source and target paths
        from_candidates = file_to_import_candidates(from_path, language_family, declared_module)
        # For declaration-based languages, infer the target declared_module
        to_declared_module = infer_target_declared_module(
            from_path, to_path, declared_module, language_family
        )
        to_candidates = file_to_import_candidates(to_path, language_family, to_declared_module)
        # Build mapping from old import string to new import string
        # The first candidate is the "canonical" form
        to_canonical = to_candidates[0] if to_candidates else self._path_to_module(to_path)
        replacement_map: dict[str, str] = {}
        for fc in from_candidates:
            # Map each from variant to the corresponding to variant
            # For dotted paths, preserve structure
            if to_candidates and len(to_candidates) > 1:
                # If from has src. prefix, use to with src. prefix (first candidate)
                # If from lacks src. prefix, use to without it (second candidate)
                if fc.startswith("src.") and to_candidates[0].startswith("src."):
                    replacement_map[fc] = to_candidates[0]
                elif not fc.startswith("src.") and len(to_candidates) > 1:
                    replacement_map[fc] = to_candidates[1]
                else:
                    replacement_map[fc] = to_canonical
            else:
                replacement_map[fc] = to_canonical
        # Get SQL patterns for querying ImportFact
        exact_matches, prefix_patterns = file_to_import_sql_patterns(
            from_path, language_family, declared_module
        )
        # Also match bare name for Python "import foo" style
        sep = get_module_separator(language_family)
        bare_names: list[str] = []
        for candidate in from_candidates:
            bare = candidate.rsplit(sep, 1)[-1] if sep in candidate else candidate
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
            if from_candidates:
                conditions.append(col(ImportFact.imported_name).in_(from_candidates))
            if not conditions:
                # No SQL conditions - skip to lexical fallback
                pass
            else:
                stmt = (
                    select(ImportFact, File.path)
                    .join(File, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                    .where(or_(*conditions))
                )
                results = session.exec(stmt).all()
                _match_import_facts(
                    results, replacement_map, bare_names, to_canonical, sep,
                    self._repo_root, seen_locations, edits_by_file,
                )
        # Lexical fallback: search for module path strings in all files
        await self._add_move_lexical_fallback(
            from_candidates,
            to_canonical,
            replacement_map,
            from_path,
            to_path,
            seen_locations,
            edits_by_file,
        )
        # Scan comments if requested
        if include_comments:
            affected_files = set(edits_by_file.keys())
            # Check for path mentions in comments
            patterns_to_search = [(from_path, to_path)]
            for fc in from_candidates:
                patterns_to_search.append((fc, replacement_map.get(fc, to_canonical)))
            for pattern, replacement in patterns_to_search:
                await self._add_comment_occurrences(
                    pattern, replacement, affected_files, edits_by_file
                )
        # Build preview
        preview = self._build_preview(edits_by_file)
        preview.move_from = from_path
        preview.move_to = to_path
        self._pending[refactor_id] = preview
        return RefactorResult(
            refactor_id=refactor_id,
            status="previewed",
            preview=preview,
        )
    async def _add_move_lexical_fallback(
        self,
        from_candidates: list[str],
        to_canonical: str,
        replacement_map: dict[str, str],
        from_path: str,
        to_path: str,
        seen_locations: set[tuple[str, int]],
        edits_by_file: dict[str, list[EditHunk]],
    ) -> None:
        """Use Tantivy index for move lexical fallback - NOT filesystem scan.
        Searches for quoted module/path strings via the index.
        Handles all language variants.
        """
        # Build search patterns: all from_candidates + file paths
        search_pairs: list[tuple[str, str]] = [(from_path, to_path)]
        for fc in from_candidates:
            # Use replacement_map to preserve prefix structure (e.g. src. prefix)
            search_pairs.append((fc, replacement_map.get(fc, to_canonical)))
        for old_val, new_val in search_pairs:
            # Search for the value (index will find files containing it)
            search_response = await self._coordinator.search(f'"{old_val}"', limit=200)
            for hit in search_response.results:
                loc = (hit.path, hit.line)
                if loc in seen_locations:
                    continue
                # Verify quoted string match
                if hit.snippet and (f'"{old_val}"' in hit.snippet or f"'{old_val}'" in hit.snippet):
                    seen_locations.add(loc)
                    edits_by_file.setdefault(hit.path, []).append(
                        EditHunk(
                            old=old_val,
                            new=new_val,
                            line=hit.line,
                            certainty="low",
                        )
                    )
    def _path_to_module(self, path: str) -> str:
        """Convert file path to Python module path."""
        # Remove .py extension and convert / to .
        module = path.replace("/", ".").replace("\\", ".")
        if module.endswith(".py"):
            module = module[:-3]
        return module


def _match_import_facts(
    results: list[Any],
    replacement_map: dict[str, str],
    bare_names: list[str],
    to_canonical: str,
    sep: str,
    repo_root: Any,
    seen_locations: set[tuple[str, int]],
    edits_by_file: dict[str, list[EditHunk]],
) -> None:
    """Match ImportFact query results and build edit hunks."""
    from pathlib import Path
    for imp, file_path in results:
        old_value = ""
        new_value = ""
        if imp.source_literal:
            if imp.source_literal in replacement_map:
                old_value = imp.source_literal
                new_value = replacement_map[imp.source_literal]
            else:
                for from_c, to_c in replacement_map.items():
                    if imp.source_literal.startswith(from_c + sep):
                        old_value = imp.source_literal
                        new_value = imp.source_literal.replace(from_c, to_c, 1)
                        break
        if not old_value:
            if imp.imported_name in replacement_map:
                old_value = imp.imported_name
                new_value = replacement_map[imp.imported_name]
            elif imp.imported_name in bare_names:
                to_bare = (
                    to_canonical.rsplit(sep, 1)[-1]
                    if sep in to_canonical
                    else to_canonical
                )
                old_value = imp.imported_name
                new_value = to_bare
        if not old_value:
            continue
        full_path = Path(repo_root) / file_path
        if not full_path.exists():
            continue
        try:
            content = full_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                if old_value in line and "import" in line.lower():
                    loc = (file_path, i)
                    if loc not in seen_locations:
                        seen_locations.add(loc)
                        cert: Literal["high", "medium", "low"] = (
                            "high" if imp.source_literal else "medium"
                        )
                        edits_by_file.setdefault(file_path, []).append(
                            EditHunk(
                                old=old_value,
                                new=new_value,
                                line=i,
                                certainty=cert,
                            )
                        )
                    break
        except (OSError, UnicodeDecodeError):
            log.debug("move_import_scan_skip", exc_info=True)
