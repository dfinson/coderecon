"""Language packs for functional/multi-paradigm languages: Scala, Elixir, Haskell, OCaml."""

from __future__ import annotations

from coderecon.index._internal.parsing.packs_base import (
    _ELIXIR_SCOPES,
    _HASKELL_SCOPES,
    _OCAML_SCOPES,
    _SCALA_SCOPES,
    ImportQueryConfig,
    LanguagePack,
    SymbolPattern,
    SymbolQueryConfig,
    TypeExtractionConfig,
)

__all__ = [
    "SCALA_PACK",
    "ELIXIR_PACK",
    "HASKELL_PACK",
    "OCAML_PACK",
]

# SCALA

_SCALA_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_definition
            name: (identifier) @name
            parameters: (parameters) @params) @node
        (class_definition
            name: (identifier) @name) @node
        (object_definition
            name: (identifier) @name) @node
        (trait_definition
            name: (identifier) @name) @node
        (val_definition
            pattern: (identifier) @name) @node
        (var_definition
            pattern: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="object"),
        SymbolPattern(kind="trait"),
        SymbolPattern(kind="val"),
        SymbolPattern(kind="var"),
    ),
    container_types=frozenset({"class_definition", "object_definition", "trait_definition"}),
)

_SCALA_TYPES = TypeExtractionConfig(
    language_family="jvm",
    scope_node_types=("function_definition", "class_definition"),
    member_access_types=("field_expression",),
    optional_patterns=("Option[",),
    array_patterns=("List[", "Seq[", "Array[", "Set[", "Vector["),
    generic_indicator="[",
    supports_interfaces=True,
    type_annotation_query="""
(parameter
  name: (identifier) @name
  (parameter_type
    (_) @type)) @param
(function_definition
  name: (identifier) @name
  return_type: (_) @type) @return
(val_definition
  pattern: (identifier) @name
  type: (_) @type)
(var_definition
  pattern: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
(class_definition
  name: (identifier) @parent
  body: (template_body
    (function_definition
      name: (identifier) @member) @method))
(class_definition
  name: (identifier) @parent
  body: (template_body
    (val_definition
      pattern: (identifier) @member
      type: (_) @type?)))
(trait_definition
  name: (identifier) @parent
  body: (template_body
    (function_definition
      name: (identifier) @member) @method))
""",
    member_access_query="""
(field_expression
  value: (identifier) @receiver
  field: (identifier) @member) @expr
(call_expression
  function: (field_expression
    value: (identifier) @receiver
    field: (identifier) @member) @expr) @call
""",
    interface_impl_query="""
(class_definition
  name: (identifier) @implementor
  (extends_clause
    (type_identifier) @interface))
""",
)

SCALA_PACK = LanguagePack(
    name="scala",
    grammar_name="scala",
    grammar_package="tree-sitter-scala",
    grammar_module="tree_sitter_scala",
    min_version="0.23.0",
    extensions=frozenset({"scala", "sc"}),
    symbol_config=_SCALA_SYMBOLS,
    scope_types=_SCALA_SCOPES,
    type_config=_SCALA_TYPES,
    sem_query="""
    (call_expression function: (identifier) @sem_call)
    (call_expression function: (field_expression field: (identifier) @sem_call))
    (assignment_expression left: (field_expression field: (identifier) @sem_field))
    (return_expression (identifier) @sem_return)
""",
    import_query_config=ImportQueryConfig(
        query="""
(import_declaration) @node
""",
        pattern_kinds={0: "scala_import"},
        source_from_node_text=True,
    ),
    declared_module_query="(package_clause) @module_node",
    declared_module_handler="_declared_module_scala",
)

# ELIXIR

_ELIXIR_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (call
            target: (identifier) @_target
            (arguments (alias) @name)
            (#eq? @_target "defmodule")) @node
        (call
            target: (identifier) @_target
            (arguments
                (call
                    target: (identifier) @name
                    (arguments) @params))
            (#eq? @_target "def")) @node
        (call
            target: (identifier) @_target
            (arguments
                (call
                    target: (identifier) @name
                    (arguments) @params))
            (#eq? @_target "defp")) @node
        (call
            target: (identifier) @_target
            (arguments
                (call
                    target: (identifier) @name
                    (arguments) @params))
            (#eq? @_target "defmacro")) @node
        (call
            target: (identifier) @_target
            (arguments
                (call
                    target: (identifier) @name
                    (arguments) @params))
            (#eq? @_target "defmacrop")) @node
        (call
            target: (identifier) @_target
            (arguments (alias) @name)
            (#eq? @_target "defprotocol")) @node
        (call
            target: (identifier) @_target
            (arguments (alias) @name)
            (#eq? @_target "defimpl")) @node
        (call
            target: (identifier) @_target
            (arguments
                (call
                    target: (identifier) @name))
            (#eq? @_target "defstruct")) @node
    """,
    patterns=(
        SymbolPattern(kind="module"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="private_function"),
        SymbolPattern(kind="macro"),
        SymbolPattern(kind="private_macro"),
        SymbolPattern(kind="protocol"),
        SymbolPattern(kind="implementation"),
        SymbolPattern(kind="struct"),
    ),
)

_ELIXIR_TYPES = TypeExtractionConfig(
    language_family="elixir",
    scope_node_types=("call",),
    member_access_types=("dot",),
    supports_type_annotations=True,
    supports_interfaces=True,
    type_annotation_query="""
(unary_operator
  operator: "@"
  operand: (call
    target: (identifier) @kind
    (arguments
      (binary_operator
        left: (call
          target: (identifier) @name)
        operator: "::"
        right: (_) @type)))) @return
""",
    type_member_query="""
(call
  target: (identifier) @defmodule
  (arguments
    (alias) @parent)
  (do_block
    (call
      target: (identifier) @def_kind
      (arguments
        (call
          target: (identifier) @member)))))) @method
""",
    member_access_query="""
(dot
  left: (identifier) @receiver
  right: (identifier) @member) @expr
(call
  target: (dot
    left: (identifier) @receiver
    right: (identifier) @member) @expr) @call
""",
    interface_impl_query="""
(unary_operator
  operator: "@"
  operand: (call
    target: (identifier) @behaviour
    (arguments
      (alias) @interface)))
""",
)

ELIXIR_PACK = LanguagePack(
    name="elixir",
    grammar_name="elixir",
    grammar_package="tree-sitter-elixir",
    grammar_module="tree_sitter_elixir",
    min_version="0.3.0",
    extensions=frozenset({"ex", "exs"}),
    symbol_config=_ELIXIR_SYMBOLS,
    scope_types=_ELIXIR_SCOPES,
    type_config=_ELIXIR_TYPES,
    sem_query="""
    (call (identifier) @sem_call)
    (call (dot (alias) @sem_call))
""",
    import_query_config=ImportQueryConfig(
        query="""
(call (identifier) @_fn (arguments (alias) @source) (#match? @_fn "^(import|alias|use|require)$")) @node
""",
        pattern_kinds={0: "elixir_import"},
        kind_from_capture="_fn",
        kind_from_capture_map={
            "import": "elixir_import",
            "alias": "elixir_alias",
            "use": "elixir_use",
            "require": "elixir_require",
        },
    ),
    declared_module_query="""
        (call
            (identifier) @_target
            (arguments
                (alias) @module_node))
    """,
    declared_module_handler="_declared_module_elixir",
)

# HASKELL

_HASKELL_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function
            name: (variable) @name
            patterns: (patterns) @params) @node
        (function
            name: (variable) @name) @node
        (signature
            name: (variable) @name
            type: (_) @params) @node
        (type_synomym
            name: (_) @name) @node
        (data_type
            name: (_) @name) @node
        (newtype
            name: (_) @name) @node
        (class
            name: (_) @name) @node
        (instance
            name: (_) @name) @node
        (data_constructor
            (prefix
                (constructor) @name)) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="signature"),
        SymbolPattern(kind="type_alias"),
        SymbolPattern(kind="data"),
        SymbolPattern(kind="newtype"),
        SymbolPattern(kind="type_class"),
        SymbolPattern(kind="instance"),
        SymbolPattern(kind="constructor"),
    ),
    container_types=frozenset({"class", "instance"}),
)

_HASKELL_TYPES = TypeExtractionConfig(
    language_family="haskell",
    scope_node_types=("function", "signature"),
    optional_patterns=("Maybe",),
    array_patterns=("[]", "List"),
    supports_type_annotations=True,
    supports_interfaces=True,
    type_annotation_query="""
(signature
  name: (variable) @name
  type: (_) @type) @return
(typed_expression
  expression: (variable) @name
  type: (_) @type)
""",
    type_member_query="""
(class
  name: (type) @parent
  (class_body
    (signature
      name: (variable) @member) @method))
(data
  name: (type) @parent
  (constructors
    (data_constructor
      (record
        (field
          (variable) @member
          type: (_) @type)))))
""",
    member_access_query="""
(variable) @receiver
""",
    interface_impl_query="""
(instance
  name: (type) @interface
  types: (type) @implementor)
""",
)

HASKELL_PACK = LanguagePack(
    name="haskell",
    grammar_name="haskell",
    grammar_package="tree-sitter-haskell",
    grammar_module="tree_sitter_haskell",
    min_version="0.23.0",
    extensions=frozenset({"hs", "lhs"}),
    symbol_config=_HASKELL_SYMBOLS,
    scope_types=_HASKELL_SCOPES,
    type_config=_HASKELL_TYPES,
    sem_query="""
    (apply (variable) @sem_call)
""",
    import_query_config=ImportQueryConfig(
        query="""
(import (module) @source (module) @alias) @node
(import (module) @source) @node
""",
        pattern_kinds={0: "haskell_import", 1: "haskell_import"},
    ),
    declared_module_query="""
        (header (module) @module_node)
    """,
    declared_module_handler="_declared_module_haskell",
)

# OCAML

_OCAML_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (value_definition
            (let_binding
                (value_name) @name
                (parameter) @params)) @node
        (value_definition
            (let_binding
                (value_name) @name)) @node
        (type_definition
            (type_binding
                (type_constructor) @name)) @node
        (module_definition
            (module_binding
                (module_name) @name)) @node
        (module_type_definition
            (module_type_name) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="variable"),
        SymbolPattern(kind="type"),
        SymbolPattern(kind="module"),
        SymbolPattern(kind="module_type"),
    ),
)

_OCAML_TYPES = TypeExtractionConfig(
    language_family="ocaml",
    scope_node_types=("value_definition", "let_binding"),
    member_access_types=("field_get_expression",),
    optional_patterns=("option",),
    array_patterns=("list", "array"),
    generic_indicator="'",
    supports_type_annotations=True,
    supports_interfaces=True,
    type_annotation_query="""
(let_binding
  pattern: (value_name) @name
  type: (type_constructor_path) @type)
(parameter
  pattern: (value_name) @name
  type: (_) @type) @param
""",
    type_member_query="""
(type_definition
  (type_binding
    name: (type_constructor) @parent
    body: (record_declaration
      (field_declaration
        name: (field_name) @member
        type: (_) @type))))
(object_expression
  (method_definition
    name: (method_name) @member) @method)
""",
    member_access_query="""
(field_get_expression
  record: (value_path) @receiver
  field: (field_path) @member) @expr
""",
)

OCAML_PACK = LanguagePack(
    name="ocaml",
    grammar_name="ocaml",
    grammar_package="tree-sitter-ocaml",
    grammar_module="tree_sitter_ocaml",
    min_version="0.23.0",
    language_func="language_ocaml",
    extensions=frozenset({"ml", "mli"}),
    symbol_config=_OCAML_SYMBOLS,
    scope_types=_OCAML_SCOPES,
    type_config=_OCAML_TYPES,
    sem_query="""
    (application_expression (value_path) @sem_call)
""",
    import_query_config=ImportQueryConfig(
        query="""
(open_module (module_path) @source) @node
(include_module (module_path) @source) @node
""",
        pattern_kinds={0: "ocaml_open", 1: "ocaml_include"},
    ),
    declared_module_handler="_declared_module_ocaml",
)
