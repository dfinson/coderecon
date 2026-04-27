"""Structural index helpers — string literals, sem facts, UID computation, models."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    import tree_sitter

from coderecon.core.languages import has_grammar
from coderecon.index._internal.parsing import SyntacticScope, SyntacticSymbol
from coderecon.index.models import BindTargetKind

log = structlog.get_logger(__name__)

# Maximum file size for tree-sitter parsing (bytes).  Files above this are
# still recorded in the file table for lexical search, but no structural
# facts are extracted.  Prevents pathological parse times on huge generated /
# data files.  1 MB covers >99.9 % of real source files.
_MAX_FILE_BYTES = 1_000_000

_SCOPE_SORT_LINE_WEIGHT = 10_000  # column span never exceeds this

# String literal discovery — grammar-metadata-driven (SPEC §16.5)

# Regex pattern for discovering string-related node types from grammar metadata
_STRING_NODE_PATTERN = re.compile(r"(?i).*string.*")

# Cache: grammar id → frozenset of string node type names.
# Bounded by the number of supported languages (~50) — each language
# creates one ts_language object that lives for the process lifetime.
_string_node_types_cache: dict[int, frozenset[str]] = {}

# Regex fallback for string literal extraction
_STRING_REGEX_DQ = re.compile(r'"([^"]{4,80})"')
_STRING_REGEX_SQ = re.compile(r"'([^']{4,80})'")

def _discover_string_node_types(ts_language: tree_sitter.Language) -> frozenset[str]:
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
    except (AttributeError, TypeError, RuntimeError):
        log.debug("string_node_introspection_failed", exc_info=True)
    result = frozenset(types)
    _string_node_types_cache[lang_id] = result
    return result

def _collect_string_literals(
    root_node: tree_sitter.Node,
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
    def walk(node: tree_sitter.Node) -> None:
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

# SEM_FACTS extraction — tree-sitter query driven (SPEC §16.6)

# Cache: (grammar id, ts_lang_name) → compiled query object or None.
# Bounded by the number of (language × query) pairs — O(supported languages).
_sem_query_cache: dict[tuple[int, str], Any] = {}

def _extract_sem_facts(
    root_node: tree_sitter.Node,
    content: bytes,
    ts_language: tree_sitter.Language,
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
        except (ValueError, RuntimeError, TypeError):
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
    except (RuntimeError, TypeError, ValueError):
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
    extraction: ExtractionResult, worktree_id: int, *, is_main_worktree: bool = True
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
        key=lambda s: (s.end_line - s.start_line) * _SCOPE_SORT_LINE_WEIGHT + (s.end_col - s.start_col),
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

