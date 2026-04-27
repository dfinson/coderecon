"""Structural fact extraction — per-file tree-sitter processing."""

from __future__ import annotations

import hashlib
import json
import time
import structlog
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tree_sitter

from coderecon.config.constants import MS_PER_SEC
from coderecon.core.languages import detect_language_family
from coderecon.index._internal.parsing.service import tree_sitter_service
from coderecon.index._internal.indexing.structural_helpers import (
    ExtractionResult,
    _collect_string_literals,
    _compute_def_uid,
    _compute_lexical_path,
    _discover_string_node_types,
    _extract_sem_facts,
    _extract_string_literals_regex,
    _find_containing_scope,
    _has_grammar_for_family,
    _MAX_FILE_BYTES,
)
from coderecon.index.models import (
    BindReasonCode, BindTargetKind, Certainty,
    RefTier, Role,
)

log = structlog.get_logger(__name__)

def _extract_file(file_path: str, repo_root: str, unit_id: int) -> ExtractionResult:
    """Extract all facts from a single file (worker function).
    Extracts: DefFact, RefFact, ScopeFact, ImportFact, LocalBindFact, DynamicAccessSite
    Files whose language has no tree-sitter grammar (e.g., F#, VB.NET, Erlang)
    are gracefully skipped - they will still be indexed in Tantivy for lexical
    search, but no structural facts are extracted.
    """
    start = time.monotonic()
    result = ExtractionResult(file_path=file_path)
    try:
        full_path = Path(repo_root) / file_path
        if not full_path.exists():
            result.error = "File not found"
            return result
        # Skip files that are too large for tree-sitter (data files, generated
        # code, minified bundles etc.).  They still appear in the file table
        # for lexical search but get no structural facts.
        try:
            file_size = full_path.stat().st_size
        except OSError:
            result.error = "Cannot stat file"
            return result
        if file_size > _MAX_FILE_BYTES:
            result.error = f"Skipped: {file_size} bytes exceeds limit"
            return result
        content = full_path.read_bytes()
        result.content_hash = hashlib.sha256(content).hexdigest()
        result.line_count = content.count(b"\n") + (
            1 if content and not content.endswith(b"\n") else 0
        )
        # Decode to UTF-8 text for lexical index (single-pass)
        try:
            result.content_text = content.decode("utf-8")
        except UnicodeDecodeError:
            result.content_text = ""
        family = detect_language_family(file_path)
        result.language_family = family
        # Check if grammar is available BEFORE attempting to parse
        # This gracefully handles languages like F#, VB.NET, Erlang that
        # have language definitions but no PyPI-available tree-sitter grammar
        if not _has_grammar_for_family(family):
            result.skipped_no_grammar = True
            result.parse_time_ms = int((time.monotonic() - start) * MS_PER_SEC)
            return result
        parser = tree_sitter_service.parser
        try:
            parse_result = parser.parse(full_path, content)
        except ValueError as e:
            result.error = str(e)
            return result
        # Extract symbols (for DefFact)
        symbols = parser.extract_symbols(parse_result)
        result.interface_hash = parser.compute_interface_hash(symbols)
        # Populate symbol names for Tantivy lexical indexing (single-pass)
        result.symbol_names = [s.name for s in symbols]
        # Extract scopes (for ScopeFact)
        scopes = parser.extract_scopes(parse_result)
        # Extract imports (for ImportFact)
        imports = parser.extract_imports(parse_result, file_path)
        # Extract declared module/package/namespace identity
        result.declared_module = parser.extract_declared_module(parse_result, file_path)
        # Extract dynamic accesses (for DynamicAccessSite)
        dynamics = parser.extract_dynamic_accesses(parse_result)
        # Track language for cross-file resolution
        result.language = parse_result.language
        # Build type_name -> namespace inversion map (C# only, empty for others)
        _type_to_ns: dict[str, str] = {}
        if parse_result.language == "csharp":
            result.namespace_type_map = parser.extract_csharp_namespace_types(
                parse_result.root_node
            )
            for _ns_name, _type_names in result.namespace_type_map.items():
                for _tname in _type_names:
                    _type_to_ns[_tname] = _ns_name
        # Convert scopes to dicts with file-local IDs (resolved to DB IDs during insert)
        for scope in scopes:
            scope_dict = {
                "unit_id": unit_id,
                "local_scope_id": scope.scope_id,  # File-local ID
                "parent_local_scope_id": scope.parent_scope_id,  # File-local parent ID
                "kind": scope.kind,
                "start_line": scope.start_line,
                "start_col": scope.start_col,
                "end_line": scope.end_line,
                "end_col": scope.end_col,
            }
            result.scopes.append(scope_dict)
        def_uid_by_name, def_scope_by_name, _def_by_scope_name, _scope_parent = _process_symbols(
            symbols, scopes, result, unit_id, file_path, _type_to_ns,
        )
        # Extract string literals per def for LIT_HINTS (SPEC §16.5)
        string_types = frozenset[str]()
        if parse_result.ts_language is not None:
            string_types = _discover_string_node_types(parse_result.ts_language)
        for def_dict in result.defs:
            sl = int(def_dict["start_line"])
            el = int(def_dict["end_line"])
            if string_types:
                literals = _collect_string_literals(
                    parse_result.root_node,
                    content,
                    sl - 1,  # tree-sitter uses 0-indexed lines
                    el - 1,
                    string_types,
                )
            else:
                # Regex fallback when grammar doesn't expose string node types
                literals = _extract_string_literals_regex(
                    result.content_text or "",
                    sl,
                    el,
                )
            if literals:
                def_dict["_string_literals"] = literals
        # Extract SEM_FACTS per def via tree-sitter queries (SPEC §16.6)
        if parse_result.ts_language is not None and parse_result.language:
            _extract_sem_facts(
                parse_result.root_node,
                content,
                parse_result.ts_language,
                parse_result.language,
                result.defs,
            )
        import_uid_by_alias = _process_imports(imports, result, unit_id)
        _process_identifier_refs(
            parser, parse_result, scopes, result, unit_id,
            def_uid_by_name, _def_by_scope_name, _scope_parent, import_uid_by_alias,
        )
        # Convert dynamic accesses to DynamicAccessSite dicts
        for dyn in dynamics:
            dyn_dict = {
                "unit_id": unit_id,
                "start_line": dyn.start_line,
                "start_col": dyn.start_col,
                "pattern_type": dyn.pattern_type,
                "extracted_literals": json.dumps(dyn.extracted_literals)
                if dyn.extracted_literals
                else None,
                "has_non_literal_key": dyn.has_non_literal_key,
            }
            result.dynamic_sites.append(dyn_dict)
        # Extract type-aware facts (Tier 2) using language-specific extractors
        _extract_type_aware_facts(result, parse_result, content, unit_id, file_path)
        result.parse_time_ms = int((time.monotonic() - start) * MS_PER_SEC)
    except (OSError, UnicodeDecodeError, RuntimeError, ValueError) as e:
        result.error = str(e)
    return result

def _extract_type_aware_facts(
    extraction: ExtractionResult,
    tree: tree_sitter.Tree,
    _content: bytes,
    unit_id: int,
    file_path: str,
) -> None:
    """Extract type-aware facts using language-specific extractors.
    Populates extraction.type_annotations, type_members, member_accesses.
    This is called after the base extraction for Tier 2 indexing.
    """
    try:
        from coderecon.index._internal.extraction import get_registry
        from coderecon.index._internal.parsing.packs import get_pack
        language = extraction.language
        if not language:
            return
        pack = get_pack(language)
        if pack is None or pack.type_config is None:
            return
        registry = get_registry()
        extractor = registry.get_or_fallback(pack.type_config.language_family)
        # Extract type annotations
        annotations = extractor.extract_type_annotations(tree, file_path, extraction.scopes)
        for ann in annotations:
            extraction.type_annotations.append(
                {
                    "unit_id": unit_id,
                    "target_kind": ann.target_kind,
                    "target_name": ann.target_name,
                    "raw_annotation": ann.raw_annotation,
                    "canonical_type": ann.canonical_type,
                    "base_type": ann.base_type,
                    "is_optional": ann.is_optional,
                    "is_array": ann.is_array,
                    "is_generic": ann.is_generic,
                    "is_reference": ann.is_reference,
                    "is_mutable": ann.is_mutable,
                    "start_line": ann.start_line,
                    "start_col": ann.start_col,
                }
            )
        # Extract type members
        members = extractor.extract_type_members(tree, file_path, extraction.defs)
        for mem in members:
            extraction.type_members.append(
                {
                    "unit_id": unit_id,
                    "parent_def_uid": mem.parent_def_uid,
                    "parent_type_name": mem.parent_type_name,
                    "parent_kind": mem.parent_kind,
                    "member_kind": mem.member_kind,
                    "member_name": mem.member_name,
                    "member_def_uid": mem.member_def_uid,
                    "type_annotation": mem.type_annotation,
                    "canonical_type": mem.canonical_type,
                    "base_type": mem.base_type,
                    "visibility": mem.visibility,
                    "is_static": mem.is_static,
                    "is_abstract": mem.is_abstract,
                    "start_line": mem.start_line,
                    "start_col": mem.start_col,
                }
            )
        # Extract member accesses
        accesses = extractor.extract_member_accesses(
            tree, file_path, extraction.scopes, annotations
        )
        for acc in accesses:
            extraction.member_accesses.append(
                {
                    "unit_id": unit_id,
                    "access_style": acc.access_style,
                    "full_expression": acc.full_expression,
                    "receiver_name": acc.receiver_name,
                    "member_chain": acc.member_chain,
                    "final_member": acc.final_member,
                    "chain_depth": acc.chain_depth,
                    "is_invocation": acc.is_invocation,
                    "arg_count": acc.arg_count,
                    "start_line": acc.start_line,
                    "start_col": acc.start_col,
                    "end_line": acc.end_line,
                    "end_col": acc.end_col,
                }
            )
        # Extract interface implementations (if extractor supports it)
        impls = extractor.extract_interface_impls(tree, file_path, extraction.defs)
        for impl in impls:
            extraction.interface_impls.append(
                {
                    "unit_id": unit_id,
                    "implementor_def_uid": impl.implementor_def_uid,
                    "implementor_name": impl.implementor_name,
                    "interface_name": impl.interface_name,
                    "interface_def_uid": impl.interface_def_uid,
                    "impl_style": impl.impl_style,
                    "start_line": impl.start_line,
                    "start_col": impl.start_col,
                }
            )
    except ImportError:
        # Extraction module not available - skip type-aware extraction
        log.debug("type_extraction_module_unavailable")
    except (RuntimeError, TypeError, ValueError):
        # Don't fail extraction for type-aware facts — they're supplementary
        log.debug("type_aware_facts_failed", exc_info=True)


def _process_symbols(
    symbols: list,
    scopes: list,
    result: ExtractionResult,
    unit_id: int,
    file_path: str,
    type_to_ns: dict[str, str],
) -> tuple[dict[str, str], dict[str, int], dict[tuple[int, str], str], dict[int, int]]:
    """Convert parsed symbols to DefFact/RefFact/LocalBindFact dicts.

    Returns (def_uid_by_name, def_scope_by_name, def_by_scope_name, scope_parent).
    """
    def_uid_by_name: dict[str, str] = {}
    def_scope_by_name: dict[str, int] = {}
    _def_by_scope_name: dict[tuple[int, str], str] = {}
    _scope_parent: dict[int, int] = {0: -1}
    for scope in scopes:
        parent = scope.parent_scope_id if scope.parent_scope_id is not None else 0
        if scope.scope_id == 0:
            continue
        _scope_parent[scope.scope_id] = parent
    disambiguator_counts: dict[tuple[str, str | None], int] = {}
    for sym in symbols:
        sig_hash = (
            hashlib.sha256((sym.signature or "").encode()).hexdigest()[:8]
            if sym.signature
            else None
        )
        lexical_path = _compute_lexical_path(sym, symbols)
        key = (lexical_path, sig_hash)
        disambiguator = disambiguator_counts.get(key, 0)
        disambiguator_counts[key] = disambiguator + 1
        def_uid = _compute_def_uid(
            unit_id, file_path, sym.kind, lexical_path, sig_hash, disambiguator
        )
        containing_scope = _find_containing_scope(scopes, sym.line, sym.column)
        lp = _compute_lexical_path(sym, symbols)
        def_dict = {
            "def_uid": def_uid,
            "unit_id": unit_id,
            "kind": sym.kind,
            "name": sym.name,
            "qualified_name": lp if "." in lp else None,
            "lexical_path": lp,
            "namespace": type_to_ns.get(sym.name),
            "start_line": sym.line,
            "start_col": sym.column,
            "end_line": sym.end_line,
            "end_col": sym.end_column,
            "signature_hash": sig_hash,
            "display_name": sym.signature,
            "signature_text": sym.signature_text,
            "decorators_json": (json.dumps(sym.decorators) if sym.decorators else None),
            "docstring": sym.docstring,
            "return_type": sym.return_type,
        }
        result.defs.append(def_dict)
        def_uid_by_name[sym.name] = def_uid
        def_scope_by_name[sym.name] = containing_scope
        _def_by_scope_name[(containing_scope, sym.name)] = def_uid
        ref_dict = {
            "unit_id": unit_id,
            "token_text": sym.name,
            "start_line": sym.line,
            "start_col": sym.column,
            "end_line": sym.end_line,
            "end_col": sym.end_column,
            "role": Role.DEFINITION.value,
            "ref_tier": RefTier.PROVEN.value,
            "certainty": Certainty.CERTAIN.value,
            "target_def_uid": def_uid,
            "local_scope_id": containing_scope,
        }
        result.refs.append(ref_dict)
        bind_dict = {
            "unit_id": unit_id,
            "name": sym.name,
            "target_kind": BindTargetKind.DEF.value,
            "target_uid": def_uid,
            "certainty": Certainty.CERTAIN.value,
            "reason_code": BindReasonCode.DEF_IN_SCOPE.value,
            "local_scope_id": containing_scope,
        }
        result.binds.append(bind_dict)
    return def_uid_by_name, def_scope_by_name, _def_by_scope_name, _scope_parent


def _process_imports(
    imports: list,
    result: ExtractionResult,
    unit_id: int,
) -> dict[str, str]:
    """Convert parsed imports to ImportFact/LocalBindFact/RefFact dicts.

    Returns import_uid_by_alias mapping.
    """
    import_uid_by_alias: dict[str, str] = {}
    for imp in imports:
        import_dict = {
            "import_uid": imp.import_uid,
            "unit_id": unit_id,
            "scope_id": None,
            "imported_name": imp.imported_name,
            "alias": imp.alias,
            "source_literal": imp.source_literal,
            "import_kind": imp.import_kind,
            "certainty": Certainty.CERTAIN.value,
            "start_line": imp.start_line,
            "start_col": imp.start_col,
            "end_line": imp.end_line,
            "end_col": imp.end_col,
            "_start_line": imp.start_line,
            "_start_col": imp.start_col,
        }
        result.imports.append(import_dict)
        local_name = imp.alias or imp.imported_name
        import_uid_by_alias[local_name] = imp.import_uid
        bind_dict = {
            "unit_id": unit_id,
            "name": local_name,
            "target_kind": BindTargetKind.IMPORT.value,
            "target_uid": imp.import_uid,
            "certainty": Certainty.CERTAIN.value,
            "reason_code": BindReasonCode.IMPORT_ALIAS.value,
            "local_scope_id": imp.scope_id or 0,
        }
        result.binds.append(bind_dict)
        ref_dict = {
            "unit_id": unit_id,
            "token_text": imp.imported_name,
            "start_line": imp.start_line,
            "start_col": imp.start_col,
            "end_line": imp.end_line,
            "end_col": imp.end_col,
            "role": Role.IMPORT.value,
            "ref_tier": RefTier.UNKNOWN.value,
            "certainty": Certainty.CERTAIN.value,
            "target_def_uid": None,
            "local_scope_id": imp.scope_id or 0,
        }
        result.refs.append(ref_dict)
    return import_uid_by_alias


def _process_identifier_refs(
    parser: object,
    parse_result: object,
    scopes: list,
    result: ExtractionResult,
    unit_id: int,
    def_uid_by_name: dict[str, str],
    def_by_scope_name: dict[tuple[int, str], str],
    scope_parent: dict[int, int],
    import_uid_by_alias: dict[str, str],
) -> None:
    """Extract identifier occurrences and build reference RefFacts."""
    occurrences = parser.extract_identifier_occurrences(parse_result)  # type: ignore[attr-defined]
    for occ in occurrences:
        is_def_site = any(
            d["name"] == occ.name
            and d["start_line"] == occ.line
            and d["start_col"] == occ.column
            for d in result.defs
        )
        if is_def_site:
            continue
        is_import_site = any(
            i["imported_name"] == occ.name and i["_start_line"] == occ.line
            for i in result.imports
        )
        if is_import_site:
            continue
        containing_scope = _find_containing_scope(scopes, occ.line, occ.column)
        ref_tier = RefTier.UNKNOWN.value
        target_def_uid = None
        certainty = Certainty.UNCERTAIN.value
        _resolved_scope_def = False
        _walk_scope = containing_scope
        while _walk_scope >= 0:
            _scope_key = (_walk_scope, occ.name)
            if _scope_key in def_by_scope_name:
                ref_tier = RefTier.PROVEN.value
                target_def_uid = def_by_scope_name[_scope_key]
                certainty = Certainty.CERTAIN.value
                _resolved_scope_def = True
                break
            _walk_scope = scope_parent.get(_walk_scope, -1)
        if not _resolved_scope_def and occ.name in def_uid_by_name:
            ref_tier = RefTier.PROVEN.value
            target_def_uid = def_uid_by_name[occ.name]
            certainty = Certainty.CERTAIN.value
        elif not _resolved_scope_def and occ.name in import_uid_by_alias:
            ref_tier = RefTier.STRONG.value
            certainty = Certainty.CERTAIN.value
        ref_dict = {
            "unit_id": unit_id,
            "token_text": occ.name,
            "start_line": occ.line,
            "start_col": occ.column,
            "end_line": occ.end_line,
            "end_col": occ.end_column,
            "role": Role.REFERENCE.value,
            "ref_tier": ref_tier,
            "certainty": certainty,
            "target_def_uid": target_def_uid,
            "local_scope_id": containing_scope,
        }
        result.refs.append(ref_dict)

