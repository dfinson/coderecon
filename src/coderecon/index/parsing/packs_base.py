"""LanguagePack base classes, config dataclasses, and scope type mappings.

Extracted from packs.py — contains all type definitions and scope
dictionaries shared across language pack modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "SymbolPattern",
    "SymbolQueryConfig",
    "TypeExtractionConfig",
    "ImportQueryConfig",
    "DeclaredModuleQueryConfig",
    "DynamicQueryConfig",
    "LanguagePack",
    "_GENERIC_SCOPE_PATTERNS",
    "_PYTHON_SCOPES",
    "_JS_SCOPES",
    "_GO_SCOPES",
    "_RUST_SCOPES",
    "_JAVA_SCOPES",
    "_CSHARP_SCOPES",
    "_KOTLIN_SCOPES",
    "_SCALA_SCOPES",
    "_RUBY_SCOPES",
    "_PHP_SCOPES",
    "_SWIFT_SCOPES",
    "_ELIXIR_SCOPES",
    "_HASKELL_SCOPES",
    "_C_CPP_SCOPES",
    "_LUA_SCOPES",
    "_JULIA_SCOPES",
    "_OCAML_SCOPES",
]

# Dataclasses

@dataclass(frozen=True)
class SymbolPattern:
    """Maps a query pattern index to symbol extraction metadata."""
    kind: str
    nested_kind: str | None = None  # Kind when inside a container

@dataclass(frozen=True)
class SymbolQueryConfig:
    """Query configuration for symbol extraction in a language.
    Defines S-expression patterns and how to interpret their matches.
    """
    query_text: str = ""
    patterns: tuple[SymbolPattern, ...] = ()
    container_types: frozenset[str] = frozenset()
    container_name_field: str = "name"
    params_from_children: bool = False
    body_node_types: frozenset[str] = frozenset({"block", "body", "class_body"})

@dataclass(frozen=True)
class TypeExtractionConfig:
    """Configuration for query-based type extraction.
    Used by QueryBasedExtractor to extract type annotations, type members,
    member accesses, and interface implementations.
    """
    language_family: str = ""
    # Query patterns (S-expression strings)
    type_annotation_query: str = ""
    type_member_query: str = ""
    member_access_query: str = ""
    interface_impl_query: str = ""
    # Node types that create scopes
    scope_node_types: tuple[str, ...] = ()
    # Member access configuration
    member_access_types: tuple[str, ...] = ("attribute",)
    member_identifier_types: tuple[str, ...] = (
        "identifier",
        "property_identifier",
        "field_identifier",
    )
    access_styles: tuple[str, ...] = ("dot",)
    # Feature flags
    supports_type_annotations: bool = True
    supports_interfaces: bool = False
    # Type normalization
    optional_patterns: tuple[str, ...] = ()
    array_patterns: tuple[str, ...] = ()
    generic_indicator: str = "<"
    reference_indicator: str = ""

# Declarative extraction configs (replace per-language Python handlers)

@dataclass(frozen=True)
class ImportQueryConfig:
    """Declarative import extraction via multi-pattern tree-sitter queries.
    Each pattern captures structured fields directly:
      @source  - source module/path
      @name    - imported symbol name
      @alias   - local alias (for rename imports)
      @node    - container node (for line/col)
    ``pattern_kinds`` maps each pattern index to an ``import_kind`` string
    (e.g., ``{0: "python_import", 1: "python_from"}``).
    The generic handler iterates query matches and constructs
    ``SyntacticImport`` objects from captures — no per-language Python code
    is needed.
    """
    query: str
    pattern_kinds: dict[int, str]
    source_capture: str = "source"
    name_capture: str = "name"
    alias_capture: str = "alias"
    strip_quotes: bool = False
    # Per-pattern overrides: pattern_idx → fixed imported_name
    name_overrides: dict[int, str] = field(default_factory=dict)
    # Per-pattern: use last path segment as imported_name (e.g., "path/filepath" → "filepath")
    name_from_source_segment: bool = False
    # Separator for splitting source paths into segments (default: "/")
    source_segment_sep: str = "/"
    # Also strip angle brackets (for C/C++ system includes)
    strip_angle_brackets: bool = False
    # Capture name that holds the function name (e.g., @_fn for require/import)
    # Used to derive import_kind from function name
    kind_from_capture: str | None = None
    # Map function names to import_kinds (e.g., {"require_relative": "ruby_require_relative"})
    kind_from_capture_map: dict[str, str] = field(default_factory=dict)
    # For languages where @node.text IS the import text (Scala flat AST)
    source_from_node_text: bool = False

@dataclass(frozen=True)
class DeclaredModuleQueryConfig:
    """Declarative module/package declaration extraction.
    Captures ``@module_name`` from the AST.  For languages that derive
    module identity from the file path (OCaml, Python), set
    ``use_file_path=True``.
    """
    query: str = ""
    module_capture: str = "module_name"
    use_file_path: bool = False

@dataclass(frozen=True)
class DynamicQueryConfig:
    """Declarative dynamic-access-pattern extraction.
    Each pattern identifies a different kind of dynamic access.
    ``pattern_types`` maps pattern index → pattern_type string
    (e.g., ``{0: "bracket_access", 1: "eval_call"}``).
    Optional ``@literal`` capture extracts string-literal keys from
    dynamic accesses.
    """
    query: str
    pattern_types: dict[int, str]
    literal_capture: str = "literal"

@dataclass(frozen=True)
class LanguagePack:
    """Complete tree-sitter configuration for a single language."""
    # -- Identity --
    name: str  # Canonical language name ("python", "typescript", ...)
    grammar_name: str  # tree-sitter grammar module key ("c_sharp", "tsx", ...)
    # -- Grammar install --
    grammar_package: str  # PyPI package ("tree-sitter-python")
    grammar_module: str  # Python import ("tree_sitter_python")
    min_version: str  # Minimum version
    # Non-standard function name (e.g. "language_typescript", "language_tsx")
    language_func: str | None = None
    # -- File detection --
    extensions: frozenset[str] = field(default_factory=frozenset)
    filenames: frozenset[str] = field(default_factory=frozenset)
    # -- Symbol extraction --
    symbol_config: SymbolQueryConfig | None = None
    # -- Scope extraction (node_type -> scope_kind) --
    scope_types: dict[str, str] = field(default_factory=dict)
    # -- SEM_FACTS body evidence --
    sem_query: str | None = None
    # -- Type extraction --
    type_config: TypeExtractionConfig | None = None
    # -- Declarative import extraction --
    import_query_config: ImportQueryConfig | None = None
    # -- Declared module detection --
    declared_module_query: str | None = None
    declared_module_handler: str | None = None
    # -- Declarative module detection (replaces declared_module_query + handler) --
    declared_module_config: DeclaredModuleQueryConfig | None = None
    # -- Dynamic access detection (tree-sitter query to find dynamic nodes) --
    dynamic_query: str | None = None
    dynamic_handler: str | None = None
    # -- Declarative dynamic detection (replaces dynamic_query + handler) --
    dynamic_config: DynamicQueryConfig | None = None

# Scope type mappings

_PYTHON_SCOPES: dict[str, str] = {
    "class_definition": "class",
    "function_definition": "function",
    "lambda": "lambda",
    "list_comprehension": "comprehension",
    "set_comprehension": "comprehension",
    "dictionary_comprehension": "comprehension",
    "generator_expression": "comprehension",
}

_JS_SCOPES: dict[str, str] = {
    "class_declaration": "class",
    "class_expression": "class",
    "function_declaration": "function",
    "function_expression": "function",
    "arrow_function": "function",
    "method_definition": "function",
    "for_statement": "block",
    "for_in_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
    "statement_block": "block",
}

_GO_SCOPES: dict[str, str] = {
    "function_declaration": "function",
    "method_declaration": "function",
    "func_literal": "function",
    "type_declaration": "class",
    "for_statement": "block",
    "if_statement": "block",
    "block": "block",
}

_RUST_SCOPES: dict[str, str] = {
    "function_item": "function",
    "impl_item": "class",
    "struct_item": "class",
    "enum_item": "class",
    "trait_item": "class",
    "closure_expression": "lambda",
    "for_expression": "block",
    "if_expression": "block",
    "match_expression": "block",
    "block": "block",
}

_JAVA_SCOPES: dict[str, str] = {
    "class_declaration": "class",
    "interface_declaration": "class",
    "enum_declaration": "class",
    "method_declaration": "function",
    "constructor_declaration": "function",
    "lambda_expression": "lambda",
    "for_statement": "block",
    "enhanced_for_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
    "try_statement": "block",
    "block": "block",
}

_CSHARP_SCOPES: dict[str, str] = {
    "class_declaration": "class",
    "struct_declaration": "class",
    "interface_declaration": "class",
    "enum_declaration": "class",
    "record_declaration": "class",
    "method_declaration": "function",
    "constructor_declaration": "function",
    "lambda_expression": "lambda",
    "for_statement": "block",
    "foreach_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
    "try_statement": "block",
    "block": "block",
}

_KOTLIN_SCOPES: dict[str, str] = {
    "class_declaration": "class",
    "object_declaration": "class",
    "function_declaration": "function",
    "lambda_literal": "lambda",
    "for_statement": "block",
    "while_statement": "block",
    "if_expression": "block",
}

_SCALA_SCOPES: dict[str, str] = {
    "class_definition": "class",
    "object_definition": "class",
    "trait_definition": "class",
    "function_definition": "function",
    "val_definition": "function",
    "for_expression": "block",
    "if_expression": "block",
    "match_expression": "block",
    "block": "block",
}

_RUBY_SCOPES: dict[str, str] = {
    "class": "class",
    "module": "class",
    "method": "function",
    "singleton_method": "function",
    "block": "block",
    "do_block": "block",
    "lambda": "lambda",
    "if": "block",
    "while": "block",
    "for": "block",
}

_PHP_SCOPES: dict[str, str] = {
    "class_declaration": "class",
    "interface_declaration": "class",
    "trait_declaration": "class",
    "method_declaration": "function",
    "function_definition": "function",
    "anonymous_function_creation_expression": "lambda",
    "arrow_function": "lambda",
    "for_statement": "block",
    "foreach_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
}

_SWIFT_SCOPES: dict[str, str] = {
    "class_declaration": "class",
    "struct_declaration": "class",
    "protocol_declaration": "class",
    "enum_declaration": "class",
    "function_declaration": "function",
    "closure_expression": "lambda",
    "for_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
}

_ELIXIR_SCOPES: dict[str, str] = {
    "call": "function",
    "anonymous_function": "lambda",
    "do_block": "block",
}

_HASKELL_SCOPES: dict[str, str] = {
    "function": "function",
    "match": "block",
    "case": "block",
    "lambda": "lambda",
}

_C_CPP_SCOPES: dict[str, str] = {
    "function_definition": "function",
    "class_specifier": "class",
    "struct_specifier": "class",
    "namespace_definition": "block",
    "for_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
    "compound_statement": "block",
}

_LUA_SCOPES: dict[str, str] = {
    "function_declaration": "function",
    "function_definition": "function",
    "for_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
    "do_statement": "block",
}

_JULIA_SCOPES: dict[str, str] = {
    "function_definition": "function",
    "macro_definition": "function",
    "struct_definition": "class",
    "module_definition": "class",
    "for_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
    "let_statement": "block",
}

_OCAML_SCOPES: dict[str, str] = {
    "let_binding": "function",
    "value_definition": "function",
    "type_definition": "class",
    "module_definition": "class",
    "match_expression": "block",
    "fun_expression": "lambda",
}

# Generic fallback: matches common node type substrings
_GENERIC_SCOPE_PATTERNS: dict[str, str] = {
    "class": "class",
    "function": "function",
    "method": "function",
    "block": "block",
    "lambda": "lambda",
}
