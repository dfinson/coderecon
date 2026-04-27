"""Language packs for systems languages: Go, Rust."""

from __future__ import annotations

from coderecon.index._internal.parsing.packs_base import (
    _GO_SCOPES,
    _RUST_SCOPES,
    ImportQueryConfig,
    LanguagePack,
    SymbolPattern,
    SymbolQueryConfig,
    TypeExtractionConfig,
)

__all__ = [
    "GO_PACK",
    "RUST_PACK",
]

# GO

_GO_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_declaration
            name: (identifier) @name
            parameters: (parameter_list) @params) @node
        (method_declaration
            name: (field_identifier) @name
            parameters: (parameter_list) @params) @node
        (type_declaration
            (type_spec
                name: (type_identifier) @name) @node)
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="type"),
    ),
)

_GO_SEM = """
    (call_expression function: (identifier) @sem_call)
    (call_expression
        function: (selector_expression
            field: (field_identifier) @sem_call))
    (return_statement
        (expression_list (identifier) @sem_return))
"""

_GO_TYPES = TypeExtractionConfig(
    language_family="go",
    scope_node_types=("function_declaration", "method_declaration"),
    member_access_types=("selector_expression",),
    member_identifier_types=("field_identifier",),
    optional_patterns=(),
    array_patterns=("[]",),
    generic_indicator="[",
    reference_indicator="*",
    supports_interfaces=True,
    type_annotation_query="""
(parameter_declaration
  name: (identifier) @name
  type: (_) @type) @param
(function_declaration
  name: (identifier) @name
  result: (_) @type) @return
(method_declaration
  name: (field_identifier) @name
  result: (_) @type) @return
(var_spec
  name: (identifier) @name
  type: (_) @type)
(const_spec
  name: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
(type_declaration
  (type_spec
    name: (type_identifier) @parent
    type: (struct_type
      (field_declaration_list
        (field_declaration
          name: (field_identifier) @member)))))
(type_declaration
  (type_spec
    name: (type_identifier) @parent
    type: (interface_type
      (method_elem
        (field_identifier) @member) @method)))
""",
    member_access_query="""
(selector_expression
  operand: (identifier) @receiver
  field: (field_identifier) @member) @expr
(call_expression
  function: (selector_expression
    operand: (identifier) @receiver
    field: (field_identifier) @member) @expr) @call
""",
    interface_impl_query="""
(type_declaration
  (type_spec
    name: (type_identifier) @implementor
    type: (struct_type
      (field_declaration_list
        (field_declaration
          type: (type_identifier) @interface
          !name)))))
(type_declaration
  (type_spec
    name: (type_identifier) @implementor
    type: (struct_type
      (field_declaration_list
        (field_declaration
          type: (qualified_type
            name: (type_identifier) @interface)
          !name)))))
""",
)

GO_PACK = LanguagePack(
    name="go",
    grammar_name="go",
    grammar_package="tree-sitter-go",
    grammar_module="tree_sitter_go",
    min_version="0.23.0",
    extensions=frozenset({"go"}),
    symbol_config=_GO_SYMBOLS,
    scope_types=_GO_SCOPES,
    sem_query=_GO_SEM,
    type_config=_GO_TYPES,
    import_query_config=ImportQueryConfig(
        query="""
; Aliased import: import fp "path/filepath"
(import_spec
  name: (package_identifier) @alias
  path: (interpreted_string_literal) @source) @node

; Dot import: import . "fmt"
(import_spec
  name: (dot)
  path: (interpreted_string_literal) @source) @node

; Blank import: import _ "net/http/pprof"
(import_spec
  name: (blank_identifier) @alias
  path: (interpreted_string_literal) @source) @node

; Plain import: import "fmt"
(import_spec
  !name
  path: (interpreted_string_literal) @source) @node
""",
        pattern_kinds={
            0: "go_import",
            1: "go_import",
            2: "go_import",
            3: "go_import",
        },
        strip_quotes=True,
        name_from_source_segment=True,
        source_segment_sep="/",
        name_overrides={1: "*"},  # dot import → wildcard
    ),
    declared_module_query="(package_clause) @module_node",
    declared_module_handler="_declared_module_go",
)

# RUST

_RUST_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_item
            name: (identifier) @name
            parameters: (parameters) @params) @node
        (struct_item
            name: (type_identifier) @name) @node
        (enum_item
            name: (type_identifier) @name) @node
        (trait_item
            name: (type_identifier) @name) @node
        (impl_item
            type: (type_identifier) @name) @node
        (type_item
            name: (type_identifier) @name) @node
        (const_item
            name: (identifier) @name) @node
        (static_item
            name: (identifier) @name) @node
        (mod_item
            name: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="struct"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="trait"),
        SymbolPattern(kind="impl"),
        SymbolPattern(kind="type_alias"),
        SymbolPattern(kind="constant"),
        SymbolPattern(kind="variable"),
        SymbolPattern(kind="module"),
    ),
    container_types=frozenset({"impl_item", "trait_item"}),
)

_RUST_SEM = """
    (call_expression function: (identifier) @sem_call)
    (call_expression
        function: (field_expression
            field: (field_identifier) @sem_call))
    (call_expression
        function: (scoped_identifier
            name: (identifier) @sem_call))
    (assignment_expression
        left: (field_expression
            field: (field_identifier) @sem_field))
    (return_expression (identifier) @sem_return)
"""

_RUST_TYPES = TypeExtractionConfig(
    language_family="rust",
    scope_node_types=("function_item", "impl_item"),
    member_access_types=("field_expression",),
    member_identifier_types=("field_identifier",),
    access_styles=("dot", "scope"),
    optional_patterns=("Option<",),
    array_patterns=("Vec<", "["),
    generic_indicator="<",
    reference_indicator="&",
    supports_interfaces=True,
    type_annotation_query="""
(parameter
  pattern: (identifier) @name
  type: (_) @type) @param
(function_item
  name: (identifier) @name
  return_type: (_) @type) @return
(let_declaration
  pattern: (identifier) @name
  type: (_) @type)
(const_item
  name: (identifier) @name
  type: (_) @type)
(static_item
  name: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
(struct_item
  name: (type_identifier) @parent
  body: (field_declaration_list
    (field_declaration
      name: (field_identifier) @member
      type: (_) @type)))
(enum_item
  name: (type_identifier) @parent
  body: (enum_variant_list
    (enum_variant
      name: (identifier) @member)))
(trait_item
  name: (type_identifier) @parent
  body: (declaration_list
    (function_signature_item
      name: (identifier) @member) @method))
(impl_item
  type: (type_identifier) @parent
  body: (declaration_list
    (function_item
      name: (identifier) @member) @method))
""",
    member_access_query="""
(field_expression
  value: (identifier) @receiver
  field: (field_identifier) @member) @expr
(call_expression
  function: (field_expression
    value: (identifier) @receiver
    field: (field_identifier) @member) @expr) @call
(scoped_identifier
  path: (identifier) @receiver
  name: (identifier) @member) @expr @scope
""",
    interface_impl_query="""
(impl_item
  trait: (type_identifier) @interface
  "for"
  type: (type_identifier) @implementor)
""",
)

RUST_PACK = LanguagePack(
    name="rust",
    grammar_name="rust",
    grammar_package="tree-sitter-rust",
    grammar_module="tree_sitter_rust",
    min_version="0.23.0",
    extensions=frozenset({"rs"}),
    symbol_config=_RUST_SYMBOLS,
    scope_types=_RUST_SCOPES,
    sem_query=_RUST_SEM,
    type_config=_RUST_TYPES,
    import_query_config=ImportQueryConfig(
        query="""
; use path::name
(use_declaration argument: (scoped_identifier) @source) @node
; use path as alias
(use_declaration argument: (use_as_clause path: (scoped_identifier) @source alias: (identifier) @alias)) @node
; use path::{item1, item2}
(use_declaration argument: (scoped_use_list path: (_) @source list: (use_list (identifier) @name))) @node
; use path::{self}
(use_declaration argument: (scoped_use_list path: (_) @source list: (use_list (self) @name))) @node
; use path::*
(use_declaration argument: (use_wildcard (scoped_identifier) @source)) @node
; use name (bare identifier)
(use_declaration argument: (identifier) @name) @node
""",
        pattern_kinds={
            0: "rust_use",
            1: "rust_use",
            2: "rust_use",
            3: "rust_use",
            4: "rust_use",
            5: "rust_use",
        },
        name_overrides={4: "*"},
        name_from_source_segment=True,
        source_segment_sep="::",
    ),
)
