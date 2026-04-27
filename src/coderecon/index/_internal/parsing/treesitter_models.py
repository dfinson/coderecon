"""Tree-sitter parsing data models."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import tree_sitter

@dataclass
class SyntacticScope:
    """A lexical scope extracted via Tree-sitter parsing."""
    scope_id: int  # Local ID within file (assigned by extractor)
    parent_scope_id: int | None  # Parent scope ID (None for file scope)
    kind: str  # file, class, function, block, comprehension, lambda
    start_line: int
    start_col: int
    end_line: int
    end_col: int
@dataclass
class SyntacticImport:
    """An import statement extracted via Tree-sitter parsing."""
    import_uid: str  # Unique ID (computed from file + line + name)
    imported_name: str  # Name being imported
    alias: str | None  # Local alias (None if no alias)
    source_literal: str | None  # Module path string (if extractable)
    import_kind: str  # python_import, python_from, js_import, etc.
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    scope_id: int | None = None  # Scope where import is visible
@dataclass
class SyntacticBind:
    """A local binding extracted via Tree-sitter parsing."""
    name: str  # Bound identifier name
    scope_id: int  # Scope where binding occurs
    target_kind: str  # DEF, IMPORT, UNKNOWN
    target_uid: str | None  # def_uid or import_uid
    reason_code: str  # PARAM, LOCAL_ASSIGN, DEF_IN_SCOPE, IMPORT_ALIAS, etc.
    start_line: int
    start_col: int
@dataclass
class DynamicAccess:
    """A dynamic access pattern detected via Tree-sitter parsing."""
    pattern_type: str  # bracket_access, getattr, reflect, eval, import_module
    start_line: int
    start_col: int
    extracted_literals: list[str] = field(default_factory=list)
    has_non_literal_key: bool = False
@dataclass
class SyntacticSymbol:
    """A symbol extracted via Tree-sitter parsing."""
    name: str
    kind: str  # function, class, method, variable, etc.
    line: int
    column: int
    end_line: int
    end_column: int
    signature: str | None = None
    parent_name: str | None = None  # For methods: the class name
    signature_text: str | None = None  # Raw signature text (params only)
    decorators: list[str] | None = None  # Decorator/annotation strings
    docstring: str | None = None  # First paragraph of docstring
    return_type: str | None = None  # Return type annotation text
@dataclass
class IdentifierOccurrence:
    """An identifier occurrence (not a semantic reference)."""
    name: str
    line: int
    column: int
    end_line: int
    end_column: int
@dataclass
class ProbeValidation:
    """Result of validating a file for context probing."""
    is_valid: bool
    error_count: int
    total_nodes: int
    has_meaningful_content: bool
    error_ratio: float = 0.0
@dataclass
class ParseResult:
    """Result of parsing a file."""
    tree: tree_sitter.Tree  # Tree-sitter Tree (not serializable)
    language: str
    error_count: int
    total_nodes: int
    root_node: tree_sitter.Node  # Tree-sitter Node
    ts_language: tree_sitter.Language | None = None  # tree-sitter Language object for grammar introspection

# C# preprocessor wrapper node types that may contain declarations.
# Tree-sitter wraps code inside #if/#region blocks under these types.
_CSHARP_PREPROC_WRAPPERS = frozenset(
    {
        "preproc_if",
        "preproc_ifdef",
        "preproc_elif",
        "preproc_else",
        "preproc_region",
    }
)

def _import_uid(file_path: str, name: str, line: int) -> str:
    """Compute stable import UID."""
    return hashlib.sha256(f"{file_path}:{line}:{name}".encode()).hexdigest()[:16]

