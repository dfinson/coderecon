"""Structural index for fact extraction.

This module provides the Tier 1 (syntactic) indexing pipeline that uses
Tree-sitter to extract facts from source files. It handles:
- Parallel file processing with worker pools
- DefFact extraction (function, class, method definitions)
- RefFact extraction (identifier occurrences)
- ScopeFact extraction (lexical scopes)
- ImportFact extraction (import statements)
- LocalBindFact extraction (same-file bindings)
- DynamicAccessSite extraction (dynamic access telemetry)

See SPEC.md §7.3 for the fact table definitions.
"""

from __future__ import annotations

import hashlib
import json
import structlog
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

log = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from coderecon.index._internal.db import Database

from coderecon.core.languages import detect_language_family, has_grammar
from coderecon.index._internal.parsing import (
    SyntacticScope,
    SyntacticSymbol,
)
from coderecon.index._internal.parsing.service import tree_sitter_service
from coderecon.index.models import (
    BindReasonCode,
    BindTargetKind,
    Certainty,
    DefFact,
    DocCrossRef,
    DynamicAccessSite,
    EndpointFact,
    File,
    ImportFact,
    InterfaceImplFact,
    LocalBindFact,
    MemberAccessFact,
    ReceiverShapeFact,
    RefFact,
    RefTier,
    Role,
    ScopeFact,
    TypeAnnotationFact,
    TypeMemberFact,
)

# Canonical list of per-file fact tables (keyed by file_id).
# Used by both the insertion loop and the removal path so they stay in sync.
# ORDER MATTERS for deletion: ScopeFact must come LAST because RefFact,
# LocalBindFact, ImportFact, TypeAnnotationFact, MemberAccessFact, and
# ReceiverShapeFact all have FK references to scope_facts.scope_id.
_FILE_FACT_TABLES: tuple[type, ...] = (
    DefFact,
    RefFact,
    LocalBindFact,
    ImportFact,
    DynamicAccessSite,
    TypeAnnotationFact,
    TypeMemberFact,
    MemberAccessFact,
    InterfaceImplFact,
    ReceiverShapeFact,
    EndpointFact,
    ScopeFact,  # LAST — other tables FK → scope_facts.scope_id
)

# Maximum file size for tree-sitter parsing (bytes).  Files above this are
# still recorded in the file table for lexical search, but no structural
# facts are extracted.  Prevents pathological parse times on huge generated /
# data files.  1 MB covers >99.9 % of real source files.
_MAX_FILE_BYTES = 1_000_000

# ===================================================================
# String literal discovery — grammar-metadata-driven (SPEC §16.5)
# ===================================================================

# Regex pattern for discovering string-related node types from grammar metadata
_STRING_NODE_PATTERN = re.compile(r"(?i).*string.*")

# Cache: grammar id → frozenset of string node type names
_string_node_types_cache: dict[int, frozenset[str]] = {}

# Regex fallback for string literal extraction
_STRING_REGEX_DQ = re.compile(r'"([^"]{4,80})"')
_STRING_REGEX_SQ = re.compile(r"'([^']{4,80})'")


def _discover_string_node_types(ts_language: Any) -> frozenset[str]:
    """Discover string literal node types from tree-sitter Language metadata.

    Scans all node kinds in the grammar for types whose name matches
    ``.*string.*`` (case-insensitive) and are marked as named nodes.
    Excludes internal repeat/content types.

    Language-agnostic: driven by grammar artifacts, not a manually
    maintained per-language list.  See SPEC.md §16.5.
    """
    lang_id = id(ts_language)
    if lang_id in _string_node_types_cache:
        return _string_node_types_cache[lang_id]

    types: set[str] = set()
    try:
        for i in range(ts_language.node_kind_count):
            name = ts_language.node_kind_for_id(i)
            if not name:
                continue
            if not ts_language.node_kind_is_named(i):
                continue
            if "repeat" in name:
                continue
            if name.startswith("_"):
                continue
            if _STRING_NODE_PATTERN.match(name):
                types.add(name)
    except Exception:
        log.debug("string_node_introspection_failed", exc_info=True)

    result = frozenset(types)
    _string_node_types_cache[lang_id] = result
    return result


def _collect_string_literals(
    root_node: Any,
    content: bytes,
    start_line: int,
    end_line: int,
    string_node_types: frozenset[str],
) -> list[str]:
    """Collect string literal content from parse tree within a def span.

    Walks the tree and extracts text from nodes whose type is in
    ``string_node_types``.  Prefers child ``string_content`` nodes;
    falls back to slicing source bytes and stripping quotes.

    Args:
        root_node: Tree-sitter root node.
        content: Source file as bytes.
        start_line: Def start line (0-indexed tree-sitter convention).
        end_line: Def end line.
        string_node_types: Set of node type names to match.

    Returns:
        List of string literal texts (unquoted, non-empty).
    """
    results: list[str] = []

    def walk(node: Any) -> None:
        # Skip nodes entirely outside the def span
        if node.end_point[0] < start_line or node.start_point[0] > end_line:
            return
        if node.type in string_node_types and node.start_point[0] >= start_line:
            # Try to get content from child nodes (e.g. string_content)
            text = None
            for child in node.children:
                if "content" in child.type.lower():
                    raw = content[child.start_byte : child.end_byte]
                    text = raw.decode("utf-8", errors="replace").strip()
                    break
            if text is None:
                # Fall back to slicing source and stripping quotes
                raw = content[node.start_byte : node.end_byte]
                text = raw.decode("utf-8", errors="replace").strip("\"'`")
            if text and 4 <= len(text) <= 80:
                results.append(text)
            return  # Don't recurse into string children
        for child in node.children:
            walk(child)

    walk(root_node)
    return results


def _extract_string_literals_regex(
    content_text: str,
    start_line: int,
    end_line: int,
) -> list[str]:
    """Regex fallback for string literal extraction.

    Used when tree-sitter grammar metadata doesn't yield string node types.
    """
    lines = content_text.split("\n")
    # Clamp to valid line range (1-indexed in def dicts, but content is 0-indexed)
    sl = max(0, start_line - 1)
    el = min(len(lines), end_line)
    source_slice = "\n".join(lines[sl:el])

    results: list[str] = []
    for match in _STRING_REGEX_DQ.finditer(source_slice):
        text = match.group(1)
        results.append(text)
    for match in _STRING_REGEX_SQ.finditer(source_slice):
        text = match.group(1)
        results.append(text)
    return results


# ===================================================================
# SEM_FACTS extraction — tree-sitter query driven (SPEC §16.6)
# ===================================================================

# Cache: (grammar id, ts_lang_name) → compiled query object or None
_sem_query_cache: dict[tuple[int, str], Any] = {}


def _extract_sem_facts(
    root_node: Any,
    content: bytes,
    ts_language: Any,
    language: str,
    defs: list[dict[str, Any]],
) -> None:
    """Extract SEM_FACTS from parse tree and assign to def dicts.

    Runs per-language tree-sitter queries once per file, then distributes
    captured semantic facts (calls, field assigns, returns, raises, key
    literals) to the def whose span contains each match.

    Modifies ``defs`` in-place: adds ``_sem_facts`` dict to each def that
    has matches.  Gracefully returns nothing when:
    - No query defined for the language
    - Query compilation fails (grammar mismatch)
    - No captures within any def span
    """
    from coderecon.index._internal.parsing.packs import get_pack

    # Mapping from tree-sitter capture names to SEM_FACTS categories
    _capture_categories = {
        "sem_call": "calls",
        "sem_field": "assigns",
        "sem_return": "returns",
        "sem_raise": "raises",
        "sem_key": "literals",
    }

    pack = get_pack(language)
    if pack is None or pack.sem_query is None:
        return
    query_text = pack.sem_query

    # Compile query (cached per grammar × language)
    cache_key = (id(ts_language), pack.grammar_name)
    if cache_key in _sem_query_cache:
        compiled = _sem_query_cache[cache_key]
    else:
        try:
            from tree_sitter import Query as _TSQuery
            from tree_sitter import QueryCursor as _TSQueryCursor  # noqa: F841

            compiled = _TSQuery(ts_language, query_text)
        except Exception:
            log.debug("sem_query_compile_failed", exc_info=True)
            compiled = None
        _sem_query_cache[cache_key] = compiled

    if compiled is None:
        return

    # Run query once over the entire file
    try:
        from tree_sitter import QueryCursor as _TSQueryCursor

        cursor = _TSQueryCursor(compiled)
        matches: list[tuple[int, dict[str, list[Any]]]] = cursor.matches(root_node)
    except Exception:
        log.debug("sem_query_execute_failed", exc_info=True)
        return

    # Collect captures: (category, raw_text, line_0idx)
    all_captures: list[tuple[str, str, int]] = []
    for _pattern_idx, captures in matches:
        for capture_name, nodes in captures.items():
            category = _capture_categories.get(capture_name)
            if not category:
                continue
            for node in nodes:
                raw = content[node.start_byte : node.end_byte]
                text = raw.decode("utf-8", errors="replace").strip("\"'`")
                if text and len(text) <= 80:
                    all_captures.append((category, text, node.start_point[0]))

    if not all_captures:
        return

    # Sort by line for efficient bucketing
    all_captures.sort(key=lambda x: x[2])

    # Distribute captures into def spans
    for def_dict in defs:
        start_line_0 = def_dict["start_line"] - 1  # tree-sitter 0-indexed
        end_line_0 = def_dict["end_line"] - 1

        facts: dict[str, list[str]] = {}
        for category, text, line in all_captures:
            if line < start_line_0 or line > end_line_0:
                continue
            cat_list = facts.setdefault(category, [])
            if text not in cat_list:
                cat_list.append(text)

        if facts:
            def_dict["_sem_facts"] = facts


def _compute_def_uid(
    unit_id: int,
    file_path: str,
    kind: str,
    lexical_path: str,
    signature_hash: str | None,
    disambiguator: int = 0,
) -> str:
    """Compute stable def_uid per SPEC.md §7.4.

    Includes file_path to distinguish same-named symbols in different files.
    Does NOT include worktree_id here; worktree scoping is applied at insert
    time by _apply_worktree_uid_remap.
    """
    sig = signature_hash or ""
    raw = f"{unit_id}:{file_path}:{kind}:{lexical_path}:{sig}:{disambiguator}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _apply_worktree_uid_remap(
    extraction: "ExtractionResult", worktree_id: int, *, is_main_worktree: bool = True
) -> None:
    """Remap def_uid and import_uid values to be scoped to a worktree.

    def_uid and import_uid are PRIMARY KEYs computed purely from syntactic
    identity (file path + symbol shape), so two worktrees indexing the same
    file would produce identical UIDs and collide on INSERT.  This function
    rewrites every UID inside the extraction dicts to include worktree_id,
    preserving all intra-extraction cross-references.

    Call this once per extraction, after extraction and before bulk insert.
    The main worktree keeps canonical UIDs; secondary worktrees get remapped.
    """
    if is_main_worktree:
        return  # main worktree keeps canonical UIDs

    def _remap(uid: str) -> str:
        return hashlib.sha256(f"{worktree_id}:{uid}".encode()).hexdigest()[:16]

    # Build remap tables from this file's own UIDs
    def_uid_remap: dict[str, str] = {
        d["def_uid"]: _remap(d["def_uid"]) for d in extraction.defs
    }
    import_uid_remap: dict[str, str] = {
        imp["import_uid"]: _remap(imp["import_uid"])
        for imp in extraction.imports
        if imp.get("import_uid")
    }

    # DefFact PKs
    for d in extraction.defs:
        d["def_uid"] = def_uid_remap[d["def_uid"]]

    # TypeMemberFact — parent_def_uid / member_def_uid
    for m in extraction.type_members:
        m["parent_def_uid"] = def_uid_remap.get(m["parent_def_uid"], m["parent_def_uid"])
        if m.get("member_def_uid"):
            m["member_def_uid"] = def_uid_remap.get(m["member_def_uid"], m["member_def_uid"])

    # InterfaceImplFact — implementor_def_uid / interface_def_uid
    for impl in extraction.interface_impls:
        impl["implementor_def_uid"] = def_uid_remap.get(
            impl["implementor_def_uid"], impl["implementor_def_uid"]
        )
        iface_uid = impl.get("interface_def_uid")
        if iface_uid:
            impl["interface_def_uid"] = def_uid_remap.get(iface_uid, iface_uid)

    # RefFact — target_def_uid when already resolved during extraction
    for r in extraction.refs:
        old_target = r.get("target_def_uid")
        if old_target:
            r["target_def_uid"] = def_uid_remap.get(old_target, old_target)

    # LocalBindFact — target_uid points to either a def_uid or import_uid
    for b in extraction.binds:
        target_uid = b.get("target_uid")
        if not target_uid:
            continue
        kind = b.get("target_kind")
        if kind == BindTargetKind.DEF.value:
            b["target_uid"] = def_uid_remap.get(target_uid, target_uid)
        elif kind == BindTargetKind.IMPORT.value:
            b["target_uid"] = import_uid_remap.get(target_uid, target_uid)

    # ImportFact PKs
    for imp in extraction.imports:
        old_uid = imp.get("import_uid")
        if old_uid and old_uid in import_uid_remap:
            imp["import_uid"] = import_uid_remap[old_uid]


def _has_grammar_for_family(language_family: str | None) -> bool:
    """Check if a language family has a tree-sitter grammar available.

    Returns True if the language has a grammar available on PyPI.
    Returns False for languages like F#, VB.NET, Erlang, etc. that lack PyPI grammars.
    Also returns False for None (unknown file types).
    """
    if language_family is None:
        return False
    return has_grammar(language_family)


@dataclass
class ExtractionResult:
    """Result of extracting facts from a single file."""

    file_path: str
    defs: list[dict[str, Any]] = field(default_factory=list)
    refs: list[dict[str, Any]] = field(default_factory=list)
    scopes: list[dict[str, Any]] = field(default_factory=list)
    imports: list[dict[str, Any]] = field(default_factory=list)
    binds: list[dict[str, Any]] = field(default_factory=list)
    dynamic_sites: list[dict[str, Any]] = field(default_factory=list)
    # Type-aware facts (Tier 2)
    type_annotations: list[dict[str, Any]] = field(default_factory=list)
    type_members: list[dict[str, Any]] = field(default_factory=list)
    member_accesses: list[dict[str, Any]] = field(default_factory=list)
    interface_impls: list[dict[str, Any]] = field(default_factory=list)
    receiver_shapes: list[dict[str, Any]] = field(default_factory=list)
    interface_hash: str | None = None
    content_hash: str | None = None
    line_count: int = 0
    error: str | None = None
    parse_time_ms: int = 0
    # Flag indicating file was skipped due to no grammar (not an error)
    skipped_no_grammar: bool = False
    # C# namespace -> type names mapping for cross-file resolution
    namespace_type_map: dict[str, list[str]] = field(default_factory=dict)
    # Language detected for this file (used in cross-file resolution)
    language: str | None = None
    # Language family detected from file path (avoids re-detection later)
    language_family: str | None = None
    # File content as UTF-8 text (for unified single-pass lexical+structural indexing)
    content_text: str | None = None
    # Symbol names extracted from tree-sitter parse (for Tantivy symbol field)
    symbol_names: list[str] = field(default_factory=list)
    # Language-level module/package identity (e.g. 'cats.effect' from Scala
    # `package cats.effect`). Extracted by tree-sitter or config-file parsers.
    declared_module: str | None = None


@dataclass
class BatchResult:
    """Result of processing a batch of files."""

    files_processed: int = 0
    defs_extracted: int = 0
    refs_extracted: int = 0
    scopes_extracted: int = 0
    imports_extracted: int = 0
    binds_extracted: int = 0
    dynamic_sites_extracted: int = 0
    # Type-aware facts (Tier 2)
    type_annotations_extracted: int = 0
    type_members_extracted: int = 0
    member_accesses_extracted: int = 0
    interface_impls_extracted: int = 0
    receiver_shapes_extracted: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    # Count of files skipped due to no grammar (not errors)
    files_skipped_no_grammar: int = 0


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
            result.parse_time_ms = int((time.monotonic() - start) * 1000)
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

        # Build def_uid -> scope mapping for binding resolution
        def_uid_by_name: dict[str, str] = {}  # name -> def_uid (latest in file)
        def_scope_by_name: dict[str, int] = {}  # name -> local_scope_id containing def
        # Scope-aware lookup: (scope_id, name) -> def_uid
        _def_by_scope_name: dict[tuple[int, str], str] = {}
        # Scope parent chain for walking up
        _scope_parent: dict[int, int] = {0: -1}  # 0 = file scope, -1 = sentinel
        for scope in scopes:
            parent = scope.parent_scope_id if scope.parent_scope_id is not None else 0
            if scope.scope_id == 0:
                # Never overwrite the file-scope sentinel
                continue
            _scope_parent[scope.scope_id] = parent

        # Track disambiguator for symbols with same (lexical_path, sig_hash)
        disambiguator_counts: dict[tuple[str, str | None], int] = {}

        # Convert symbols to DefFact dicts
        for sym in symbols:
            sig_hash = (
                hashlib.sha256((sym.signature or "").encode()).hexdigest()[:8]
                if sym.signature
                else None
            )
            lexical_path = _compute_lexical_path(sym, symbols)

            # Compute disambiguator for same-signature siblings
            key = (lexical_path, sig_hash)
            disambiguator = disambiguator_counts.get(key, 0)
            disambiguator_counts[key] = disambiguator + 1

            def_uid = _compute_def_uid(
                unit_id, file_path, sym.kind, lexical_path, sig_hash, disambiguator
            )

            # Find containing scope
            containing_scope = _find_containing_scope(scopes, sym.line, sym.column)

            lp = _compute_lexical_path(sym, symbols)
            def_dict = {
                "def_uid": def_uid,
                "unit_id": unit_id,
                "kind": sym.kind,
                "name": sym.name,
                "qualified_name": lp if "." in lp else None,
                "lexical_path": lp,
                "namespace": _type_to_ns.get(sym.name),
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

            # Track for binding resolution
            def_uid_by_name[sym.name] = def_uid
            def_scope_by_name[sym.name] = containing_scope
            _def_by_scope_name[(containing_scope, sym.name)] = def_uid

            # Create a definition RefFact (definition sites are PROVEN refs to themselves)
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

            # Create LocalBindFact for the definition binding
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

        # Extract string literals per def for LIT_HINTS (SPEC §16.5)
        string_types = frozenset[str]()
        if parse_result.ts_language is not None:
            string_types = _discover_string_node_types(parse_result.ts_language)
        for def_dict in result.defs:
            _sl: Any = def_dict["start_line"]
            _el: Any = def_dict["end_line"]
            sl = int(_sl)
            el = int(_el)
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

        # Convert imports to ImportFact dicts and create bindings
        import_uid_by_alias: dict[str, str] = {}  # alias/name -> import_uid
        for imp in imports:
            import_dict = {
                "import_uid": imp.import_uid,
                "unit_id": unit_id,
                "scope_id": None,  # scope_id is nullable FK - will be set later if scopes are tracked
                "imported_name": imp.imported_name,
                "alias": imp.alias,
                "source_literal": imp.source_literal,
                "import_kind": imp.import_kind,
                "certainty": Certainty.CERTAIN.value,
                "start_line": imp.start_line,
                "start_col": imp.start_col,
                "end_line": imp.end_line,
                "end_col": imp.end_col,
                # Also kept for deduplication (aliased)
                "_start_line": imp.start_line,
                "_start_col": imp.start_col,
            }
            result.imports.append(import_dict)

            # Track for binding resolution
            local_name = imp.alias or imp.imported_name
            import_uid_by_alias[local_name] = imp.import_uid

            # Create LocalBindFact for import binding
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

            # Create RefFact for the import statement
            ref_dict = {
                "unit_id": unit_id,
                "token_text": imp.imported_name,
                "start_line": imp.start_line,
                "start_col": imp.start_col,
                "end_line": imp.end_line,
                "end_col": imp.end_col,
                "role": Role.IMPORT.value,
                "ref_tier": RefTier.UNKNOWN.value,  # Cross-file resolution needed
                "certainty": Certainty.CERTAIN.value,
                "target_def_uid": None,
                "local_scope_id": imp.scope_id or 0,
            }
            result.refs.append(ref_dict)

        # Extract identifier occurrences for reference RefFacts
        occurrences = parser.extract_identifier_occurrences(parse_result)
        for occ in occurrences:
            # Skip if this is already a definition site
            is_def_site = any(
                d["name"] == occ.name
                and d["start_line"] == occ.line
                and d["start_col"] == occ.column
                for d in result.defs
            )
            if is_def_site:
                continue

            # Skip if this is an import site
            is_import_site = any(
                i["imported_name"] == occ.name and i["_start_line"] == occ.line
                for i in result.imports
            )
            if is_import_site:
                continue

            containing_scope = _find_containing_scope(scopes, occ.line, occ.column)

            # Determine ref_tier and target based on local bindings
            ref_tier = RefTier.UNKNOWN.value
            target_def_uid = None
            certainty = Certainty.UNCERTAIN.value

            # Scope-aware same-file resolution: walk from innermost scope outward
            _resolved_scope_def = False
            _walk_scope = containing_scope
            while _walk_scope >= 0:
                _scope_key = (_walk_scope, occ.name)
                if _scope_key in _def_by_scope_name:
                    ref_tier = RefTier.PROVEN.value
                    target_def_uid = _def_by_scope_name[_scope_key]
                    certainty = Certainty.CERTAIN.value
                    _resolved_scope_def = True
                    break
                _walk_scope = _scope_parent.get(_walk_scope, -1)

            # Fallback to flat dict for defs not in any scope (e.g. module-level)
            if not _resolved_scope_def and occ.name in def_uid_by_name:
                ref_tier = RefTier.PROVEN.value
                target_def_uid = def_uid_by_name[occ.name]
                certainty = Certainty.CERTAIN.value
            # Check if name is an import alias
            elif not _resolved_scope_def and occ.name in import_uid_by_alias:
                ref_tier = RefTier.STRONG.value  # Cross-file with explicit trace
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

        result.parse_time_ms = int((time.monotonic() - start) * 1000)

    except Exception as e:
        result.error = str(e)

    return result


def _extract_type_aware_facts(
    extraction: ExtractionResult,
    tree: Any,
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
        pass
    except Exception:
        # Don't fail extraction for type-aware facts - they're supplementary
        log.debug("type_aware_facts_failed", exc_info=True)


def _find_containing_scope(scopes: list[SyntacticScope], line: int, col: int) -> int:
    """Find the innermost scope containing the given position.

    Returns the file-local scope_id (0 for file scope).
    """
    # Sort by specificity (smaller ranges are more specific)
    containing: list[SyntacticScope] = []
    for scope in scopes:
        if (scope.start_line < line or (scope.start_line == line and scope.start_col <= col)) and (
            scope.end_line > line or (scope.end_line == line and scope.end_col >= col)
        ):
            containing.append(scope)

    if not containing:
        return 0  # File scope

    # Return innermost (smallest range)
    innermost = min(
        containing,
        key=lambda s: (s.end_line - s.start_line) * 10000 + (s.end_col - s.start_col),
    )
    return innermost.scope_id


def _compute_lexical_path(sym: SyntacticSymbol, all_symbols: list[SyntacticSymbol]) -> str:
    """Compute the lexical path for a symbol (e.g., 'Class.method')."""
    if sym.parent_name:
        return f"{sym.parent_name}.{sym.name}"

    # For classes/functions at module level, just use the name
    if sym.kind in ("class", "function"):
        return sym.name

    # For methods, try to find the innermost containing class
    best: SyntacticSymbol | None = None
    best_span = float("inf")
    for other in all_symbols:
        if other.kind == "class" and (
            other.line <= sym.line <= other.end_line and other.column <= sym.column
        ):
            span = other.end_line - other.line
            if span < best_span:
                best = other
                best_span = span

    if best is not None:
        return f"{best.name}.{sym.name}"

    return sym.name


class StructuralIndexer:
    """Extracts facts from source files using Tree-sitter.

    This is the Tier 1 (syntactic) indexing layer. It provides:
    - DefFact extraction (function/class/method definitions)
    - RefFact extraction (identifier occurrences)
    - ScopeFact extraction (lexical scopes)
    - ImportFact extraction (import statements)
    - LocalBindFact extraction (same-file bindings)
    - DynamicAccessSite extraction (dynamic access telemetry)

    Files whose language has no tree-sitter grammar (e.g., F#, VB.NET, Erlang)
    are gracefully skipped by this indexer. They will still be searchable via
    the lexical (Tantivy) index.

    Usage::

        indexer = StructuralIndexer(db, repo_path)
        result = indexer.index_files(file_paths, context_id=1, worktree_id=wt_id)
    """

    def __init__(self, db: Database, repo_path: Path | str):
        self.db = db
        self.repo_path = Path(repo_path)

    def extract_files(
        self,
        file_paths: list[str],
        context_id: int,
        workers: int = 1,
        *,
        repo_root: Path | str | None = None,
    ) -> list[ExtractionResult]:
        """Extract facts from files without persisting.

        Returns ExtractionResult list that can be passed to
        index_files(_extractions=...) for persistence.

        Each result includes content_text and symbol_names for
        unified single-pass indexing (lexical + structural).

        ``repo_root`` overrides the default ``self.repo_path`` for file
        reading.  Pass the worktree checkout directory when indexing a
        git worktree so files are read from the correct location.
        """
        effective_root = Path(repo_root) if repo_root is not None else self.repo_path
        if workers > 1 and len(file_paths) > 1:
            return self._parallel_extract(file_paths, context_id, workers, repo_root=effective_root)
        return self._sequential_extract(file_paths, context_id, repo_root=effective_root)

    def index_files(
        self,
        file_paths: list[str],
        context_id: int,
        file_id_map: dict[str, int] | None = None,
        workers: int = 1,
        *,
        worktree_id: int,
        is_main_worktree: bool = True,
        _extractions: list[ExtractionResult] | None = None,
    ) -> BatchResult:
        """Index a batch of files.

        If _extractions is provided, uses pre-computed extraction results
        instead of extracting from disk. This enables single-pass indexing
        where the coordinator extracts once and reuses results for both
        Tantivy staging and structural fact persistence.
        """
        start = time.monotonic()
        result = BatchResult()

        if _extractions is not None:
            extractions = _extractions
        elif workers > 1:
            extractions = self._parallel_extract(file_paths, context_id, workers)
        else:
            extractions = self._sequential_extract(file_paths, context_id)

        # Augment declared_module for languages needing config file resolution
        # (Go → go.mod, Rust → Cargo.toml). Tree-sitter gives Go only the
        # short package name; the full import path needs go.mod context.
        self._augment_declared_modules(extractions)

        # Resolve import source_literal → target file path (all languages).
        # Must run after _augment_declared_modules so Go/Rust declared_modules
        # are fully resolved.  Populates import_dict["resolved_path"].
        self._resolve_import_paths(extractions)

        # Pre-create all files BEFORE entering bulk_writer to avoid lock contention
        if file_id_map is None:
            file_id_map = {}
        for extraction in extractions:
            if extraction.error:
                # Record parse failure on the File row even though we won't extract facts
                if extraction.file_path not in file_id_map:
                    file_id_map[extraction.file_path] = self._ensure_file_id(
                        extraction.file_path,
                        extraction.content_hash,
                        extraction.line_count,
                        context_id,
                        language_family=extraction.language_family,
                        worktree_id=worktree_id,
                        parse_status="failed",
                    )
                continue
            if extraction.file_path not in file_id_map:
                # Determine parse_status from extraction result
                _ps = "ok"
                if extraction.skipped_no_grammar:
                    _ps = "skipped"
                file_id_map[extraction.file_path] = self._ensure_file_id(
                    extraction.file_path,
                    extraction.content_hash,
                    extraction.line_count,
                    context_id,
                    language_family=extraction.language_family,
                    declared_module=extraction.declared_module,
                    worktree_id=worktree_id,
                    parse_status=_ps,
                )

        # Remap def_uid / import_uid to include worktree_id so that two
        # worktrees indexing the same file don't collide on PK constraints.
        for extraction in extractions:
            if not extraction.error:
                _apply_worktree_uid_remap(
                    extraction, worktree_id, is_main_worktree=is_main_worktree
                )

        with self.db.bulk_writer() as writer:
            for extraction in extractions:
                result.files_processed += 1

                if extraction.error:
                    result.errors.append(f"{extraction.file_path}: {extraction.error}")
                    continue

                # Track files skipped due to no grammar (not errors)
                if extraction.skipped_no_grammar:
                    result.files_skipped_no_grammar += 1
                    continue

                file_id = file_id_map.get(extraction.file_path)
                if file_id is None:
                    result.errors.append(f"{extraction.file_path}: File ID not found")
                    continue

                # Delete existing facts for this file (idempotent re-indexing)
                for fact_model in _FILE_FACT_TABLES:
                    writer.delete_where(fact_model, "file_id = :fid", {"fid": file_id})
                # DocCrossRef uses source_file_id, not file_id
                writer.delete_where(DocCrossRef, "source_file_id = :fid", {"fid": file_id})

                # Build local_scope_id -> db_scope_id mapping.
                # Scopes are extracted in dependency order (parent before child),
                # so parent_scope_id is always resolvable from the map.
                scope_id_map: dict[int, int] = {}  # local_scope_id -> db scope_id
                from sqlalchemy import text as _sa_text

                for scope_dict in extraction.scopes:
                    local_id = scope_dict.pop("local_scope_id")
                    parent_local_id = scope_dict.pop("parent_local_scope_id")
                    scope_dict["file_id"] = file_id
                    scope_dict["parent_scope_id"] = (
                        scope_id_map[parent_local_id]
                        if parent_local_id is not None and parent_local_id in scope_id_map
                        else None
                    )
                    writer.insert_many(ScopeFact, [scope_dict])
                    row = writer.conn.execute(_sa_text("SELECT last_insert_rowid()")).fetchone()
                    if row is not None:
                        scope_id_map[local_id] = row[0]
                    result.scopes_extracted += 1

                # Insert DefFacts
                for def_dict in extraction.defs:
                    def_dict["file_id"] = file_id
                    writer.insert_many(DefFact, [def_dict])
                    result.defs_extracted += 1

                # Insert RefFacts — resolve local_scope_id to DB scope_id
                for ref_dict in extraction.refs:
                    ref_dict["file_id"] = file_id
                    local_sid = ref_dict.pop("local_scope_id", None)
                    ref_dict["scope_id"] = (
                        scope_id_map.get(local_sid) if local_sid else None
                    )
                    writer.insert_many(RefFact, [ref_dict])
                    result.refs_extracted += 1

                # Insert ImportFacts (deduplicate by import_uid to guard
                # against extractors producing duplicates on the same line)
                seen_import_uids: set[str] = set()
                for import_dict in extraction.imports:
                    uid = import_dict.get("import_uid")
                    # Only deduplicate when we have a non-empty string UID.
                    # Imports without a usable UID should all be inserted, rather than
                    # being collapsed together under a shared empty-string key.
                    if isinstance(uid, str) and uid:
                        if uid in seen_import_uids:
                            continue
                        seen_import_uids.add(uid)
                    import_dict["file_id"] = file_id
                    # Remove internal tracking fields not in DB schema
                    import_dict.pop("_start_line", None)
                    import_dict.pop("_start_col", None)
                    writer.insert_many(ImportFact, [import_dict])
                    result.imports_extracted += 1

                # Insert LocalBindFacts — resolve local_scope_id to DB scope_id
                for bind_dict in extraction.binds:
                    bind_dict["file_id"] = file_id
                    local_sid = bind_dict.pop("local_scope_id", None)
                    bind_dict["scope_id"] = (
                        scope_id_map.get(local_sid) if local_sid else None
                    )
                    writer.insert_many(LocalBindFact, [bind_dict])
                    result.binds_extracted += 1

                # Insert DynamicAccessSites
                for dyn_dict in extraction.dynamic_sites:
                    dyn_dict["file_id"] = file_id
                    writer.insert_many(DynamicAccessSite, [dyn_dict])
                    result.dynamic_sites_extracted += 1

                # Insert TypeAnnotationFacts (Tier 2)
                for ann_dict in extraction.type_annotations:
                    ann_dict["file_id"] = file_id
                    writer.insert_many(TypeAnnotationFact, [ann_dict])
                    result.type_annotations_extracted += 1

                # Insert TypeMemberFacts (Tier 2)
                for mem_dict in extraction.type_members:
                    mem_dict["file_id"] = file_id
                    writer.insert_many(TypeMemberFact, [mem_dict])
                    result.type_members_extracted += 1

                # Insert MemberAccessFacts (Tier 2)
                for acc_dict in extraction.member_accesses:
                    acc_dict["file_id"] = file_id
                    writer.insert_many(MemberAccessFact, [acc_dict])
                    result.member_accesses_extracted += 1

                # Insert InterfaceImplFacts (Tier 2)
                for impl_dict in extraction.interface_impls:
                    impl_dict["file_id"] = file_id
                    writer.insert_many(InterfaceImplFact, [impl_dict])
                    result.interface_impls_extracted += 1

                # Insert ReceiverShapeFacts (Tier 2) - computed during resolution, not extraction
                for shape_dict in extraction.receiver_shapes:
                    shape_dict["file_id"] = file_id
                    writer.insert_many(ReceiverShapeFact, [shape_dict])
                    result.receiver_shapes_extracted += 1

                # Detect and insert EndpointFacts
                if extraction.content_text and extraction.language:
                    from coderecon.index._internal.analysis.endpoint_detection import (
                        detect_endpoints_in_source,
                    )

                    endpoints = detect_endpoints_in_source(
                        extraction.content_text, extraction.language
                    )
                    if endpoints:
                        # Build line→def_uid map for handler resolution
                        func_defs = [
                            d for d in extraction.defs
                            if d.get("kind") in ("function", "method")
                        ]
                        for ep in endpoints:
                            handler_uid = None
                            for d in func_defs:
                                if d["start_line"] <= ep.line <= d["end_line"]:
                                    handler_uid = d["def_uid"]
                            writer.insert_many(EndpointFact, [{
                                "file_id": file_id,
                                "kind": ep.kind,
                                "http_method": ep.http_method,
                                "url_pattern": ep.url_pattern,
                                "handler_def_uid": handler_uid,
                                "start_line": ep.line,
                                "end_line": ep.line,
                                "framework": ep.framework,
                            }])

                # Extract and insert DocCrossRefs from docstrings
                if extraction.defs:
                    from coderecon.index._internal.analysis.docstring_xref import (
                        RawCrossRef,
                        extract_cross_refs,
                    )

                    for def_dict in extraction.defs:
                        docstring = def_dict.get("docstring")
                        if not docstring:
                            continue
                        raw_refs = extract_cross_refs(
                            docstring, start_line=def_dict["start_line"]
                        )
                        if not raw_refs:
                            continue
                        # Resolve targets against already-persisted defs
                        # (cross-batch refs resolve on subsequent reindex)
                        for ref in raw_refs:
                            target_uid = self._resolve_xref_target(
                                writer, ref.target_name
                            )
                            if target_uid:
                                writer.insert_many(DocCrossRef, [{
                                    "source_file_id": file_id,
                                    "source_def_uid": def_dict.get("def_uid"),
                                    "source_line": ref.source_line,
                                    "raw_text": ref.raw_text,
                                    "target_def_uid": target_uid,
                                    "confidence": ref.confidence,
                                }])

        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    def _augment_declared_modules(self, extractions: list[ExtractionResult]) -> None:
        """Post-process declared_module for languages needing config files.

        Go files get only the short package name from tree-sitter (e.g.
        ``mypackage``).  This method resolves the full import path using
        ``go.mod`` (e.g. ``github.com/user/repo/pkg/mypackage``).

        Rust files have no source-level package declaration.  The crate
        name is read from ``Cargo.toml`` and combined with the directory
        structure (e.g. ``my_crate::auth::token``).

        During batched initial indexing, config files (go.mod, Cargo.toml)
        may have been indexed in an earlier batch.  We seed the resolver
        with ALL file paths from the DB so config discovery works cross-batch.
        """
        from sqlmodel import select

        from coderecon.index._internal.indexing.config_resolver import (
            ConfigResolver,
        )

        # Seed file paths from DB (cross-batch config discovery)
        all_paths_set: set[str] = set()
        with self.db.session() as session:
            rows = session.exec(select(File.path)).all()
            for path in rows:
                all_paths_set.add(path)

        # Overlay current batch paths
        for e in extractions:
            if not e.error:
                all_paths_set.add(e.file_path)

        # Config files (go.mod, Cargo.toml) are not source code so they
        # may not be indexed by tree-sitter.  Discover them from the
        # filesystem so ConfigResolver can find them.
        for pattern in ("**/go.mod", "**/Cargo.toml"):
            for cfg in self.repo_path.glob(pattern):
                all_paths_set.add(str(cfg.relative_to(self.repo_path)))

        resolver = ConfigResolver(str(self.repo_path), list(all_paths_set))

        def _read_file(rel_path: str) -> str | None:
            full = self.repo_path / rel_path
            try:
                return full.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None

        for ex in extractions:
            if ex.error or ex.skipped_no_grammar:
                continue
            lang = ex.language
            if lang in ("go", "rust"):
                resolved = resolver.resolve(
                    ex.file_path, lang, ex.declared_module, read_file=_read_file
                )
                if resolved:
                    ex.declared_module = resolved

        # Fallback: derive declared_module from file path for languages
        # that don't have source-level module declarations (e.g. Python, JS/TS)
        from coderecon.index._internal.indexing.module_mapping import path_to_module

        for ex in extractions:
            if ex.error or ex.skipped_no_grammar:
                continue
            if ex.declared_module is None:
                ex.declared_module = path_to_module(ex.file_path)

    def _resolve_xref_target(self, writer: Any, target_name: str) -> str | None:
        """Resolve a cross-ref target name to a def_uid using the BulkWriter's connection."""
        from sqlalchemy import text as sa_text

        conn = writer.conn
        # 1. Exact def_uid
        row = conn.execute(
            sa_text("SELECT def_uid FROM def_facts WHERE def_uid = :name LIMIT 1"),
            {"name": target_name},
        ).fetchone()
        if row:
            return row[0]
        # 2. Name-only match
        simple_name = target_name.rsplit(".", 1)[-1]
        row = conn.execute(
            sa_text("SELECT def_uid FROM def_facts WHERE name = :name LIMIT 1"),
            {"name": simple_name},
        ).fetchone()
        if row:
            return row[0]
        # 3. Suffix match
        row = conn.execute(
            sa_text("SELECT def_uid FROM def_facts WHERE def_uid LIKE :suffix LIMIT 1"),
            {"suffix": f"%.{target_name}"},
        ).fetchone()
        if row:
            return row[0]
        return None

    def _resolve_import_paths(self, extractions: list[ExtractionResult]) -> None:
        """Resolve every import's source_literal to a target file path.

        Populates ``import_dict["resolved_path"]`` for each import in each
        extraction.  Uses the ``ImportPathResolver`` which supports:

        - Python: dotted module → file path via ``module_mapping``
        - JS/TS: relative path resolution with extension probing
        - C/C++: relative header resolution
        - Declaration-based (Java, Kotlin, etc.): match against ``declared_module``

        During initial indexing files are processed in batches of 50.  To
        resolve cross-batch imports (e.g. a Java import in batch 3 targeting
        a class declared in batch 1) we seed the resolver with ALL file paths
        and declared_modules already persisted in the DB, then overlay the
        current batch on top (which may have fresher data).
        """
        from sqlmodel import select

        from coderecon.index._internal.indexing.config_resolver import (
            ImportPathResolver,
            build_js_package_exports,
        )
        from coderecon.index.models import File

        valid = [e for e in extractions if not e.error and not e.skipped_no_grammar]

        # Seed from DB: all previously-indexed file paths + declared_modules.
        # This ensures cross-batch resolution works during initial indexing.
        all_paths_set: set[str] = set()
        declared_modules: dict[str, str] = {}
        with self.db.session() as session:
            rows = session.exec(select(File.path, File.declared_module)).all()
            for path, dm in rows:
                all_paths_set.add(path)
                if dm:
                    declared_modules[path] = dm

        # Overlay current batch (may contain new/updated data not yet persisted).
        for e in valid:
            all_paths_set.add(e.file_path)
            if e.declared_module:
                declared_modules[e.file_path] = e.declared_module

        all_paths_list = list(all_paths_set)

        # Build JS/TS package.json exports map for bare specifier resolution
        def _read_file(rel_path: str) -> str | None:
            full = self.repo_path / rel_path
            try:
                return full.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None

        js_exports = build_js_package_exports(all_paths_list, _read_file)

        resolver = ImportPathResolver(all_paths_list, declared_modules, js_exports)

        for ex in valid:
            for imp in ex.imports:
                source_literal = imp.get("source_literal")
                import_kind = imp.get("import_kind", "")
                resolved = resolver.resolve(source_literal, import_kind, ex.file_path)
                if resolved:
                    imp["resolved_path"] = resolved

    def resolve_all_imports(self) -> int:
        """Re-resolve all unresolved import paths using the complete DB.

        Called once after all batches have been indexed so that imports
        from early batches can resolve to files indexed in later batches.

        Returns:
            Number of imports that were newly resolved.
        """
        from sqlmodel import select

        from coderecon.index._internal.indexing.config_resolver import (
            ImportPathResolver,
            build_js_package_exports,
        )
        from coderecon.index.models import File, ImportFact

        # Build resolver from the complete DB
        all_paths: list[str] = []
        declared_modules: dict[str, str] = {}
        with self.db.session() as session:
            rows = session.exec(select(File.path, File.declared_module)).all()
            for path, dm in rows:
                all_paths.append(path)
                if dm:
                    declared_modules[path] = dm

        # Build JS/TS package.json exports map for bare specifier resolution
        def _read_file(rel_path: str) -> str | None:
            full = self.repo_path / rel_path
            try:
                return full.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None

        js_exports = build_js_package_exports(all_paths, _read_file)

        resolver = ImportPathResolver(all_paths, declared_modules, js_exports)

        # Find all unresolved imports and try to resolve them
        newly_resolved = 0
        with self.db.session() as session:
            stmt = (
                select(ImportFact)
                .where(ImportFact.resolved_path.is_(None))  # type: ignore[union-attr]
                .where(ImportFact.source_literal.isnot(None))  # type: ignore[union-attr]
            )
            unresolved = list(session.exec(stmt).all())

            # Build file_id -> path mapping for importer_path lookup
            file_ids = {imp.file_id for imp in unresolved}
            file_map: dict[int, str] = {}
            if file_ids:
                file_rows = session.exec(
                    select(File.id, File.path).where(
                        File.id.in_(file_ids)  # type: ignore[union-attr]
                    )
                ).all()
                file_map = {int(fid): fp for fid, fp in file_rows if fid is not None}

            for imp in unresolved:
                importer_path = file_map.get(imp.file_id, "")
                resolved = resolver.resolve(
                    imp.source_literal, imp.import_kind or "", importer_path
                )
                if resolved:
                    imp.resolved_path = resolved
                    session.add(imp)
                    newly_resolved += 1

            if newly_resolved:
                session.commit()

        return newly_resolved

    def _sequential_extract(
        self, file_paths: list[str], unit_id: int, repo_root: Path | None = None
    ) -> list[ExtractionResult]:
        """Extract facts sequentially."""
        root = str(repo_root if repo_root is not None else self.repo_path)
        results = []
        for path in file_paths:
            result = _extract_file(path, root, unit_id)
            results.append(result)
        return results

    def _parallel_extract(
        self, file_paths: list[str], unit_id: int, workers: int, repo_root: Path | None = None
    ) -> list[ExtractionResult]:
        """Extract facts in parallel using process pool."""
        results = []
        root = str(repo_root if repo_root is not None else self.repo_path)

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_extract_file, path, root, unit_id): path
                for path in file_paths
            }

            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    path = futures[future]
                    results.append(ExtractionResult(file_path=path, error=str(e)))

        return results

    def _ensure_file_id(
        self,
        file_path: str,
        content_hash: str | None,
        line_count: int,
        _context_id: int,
        language_family: str | None = None,
        declared_module: str | None = None,
        *,
        worktree_id: int,
        parse_status: str | None = None,
    ) -> int:
        """Ensure file exists in database and return its ID."""
        import time

        with self.db.session() as session:
            from sqlmodel import select

            stmt = select(File).where(
                File.path == file_path,
                File.worktree_id == worktree_id,
            )
            existing = session.exec(stmt).first()

            if existing and existing.id is not None:
                _changed = False
                if existing.declared_module != declared_module:
                    existing.declared_module = declared_module
                    _changed = True
                if parse_status is not None and existing.parse_status != parse_status:
                    existing.parse_status = parse_status
                    _changed = True
                if content_hash is not None and existing.content_hash != content_hash:
                    existing.content_hash = content_hash
                    existing.line_count = line_count
                    _changed = True
                if _changed:
                    session.add(existing)
                    session.commit()
                return existing.id

            file = File(
                path=file_path,
                content_hash=content_hash,
                line_count=line_count,
                language_family=language_family,
                declared_module=declared_module,
                indexed_at=time.time(),
                worktree_id=worktree_id,
                parse_status=parse_status,
            )
            session.add(file)
            session.commit()
            session.refresh(file)
            if file.id is None:
                raise RuntimeError(f"Failed to allocate file id for {file_path!r}")
            return file.id

    def extract_single(self, file_path: str, unit_id: int = 0) -> ExtractionResult:
        """Extract facts from a single file without storing."""
        return _extract_file(file_path, str(self.repo_path), unit_id)

    def compute_batch_interface_hash(self, file_paths: list[str]) -> str:
        """Compute combined interface hash for multiple files."""
        hashes = []
        for path in sorted(file_paths):
            result = self.extract_single(path)
            if result.interface_hash:
                hashes.append(result.interface_hash)

        combined = "\n".join(hashes)
        return hashlib.sha256(combined.encode()).hexdigest()


def index_context(
    db: Any,
    repo_path: Path | str,
    context_id: int,
    file_paths: list[str],
    workers: int = os.cpu_count() or 1,
    *,
    worktree_id: int,
) -> BatchResult:
    """Convenience function to index all files in a context."""
    indexer = StructuralIndexer(db, repo_path)
    return indexer.index_files(file_paths, context_id, workers=workers, worktree_id=worktree_id)
