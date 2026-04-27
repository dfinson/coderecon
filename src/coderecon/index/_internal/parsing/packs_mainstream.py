"""Language packs for mainstream languages: Python, JavaScript, TypeScript, TSX."""

from __future__ import annotations

from coderecon.index._internal.parsing.packs_base import (
    ImportQueryConfig,
    LanguagePack,
    SymbolPattern,
    SymbolQueryConfig,
    TypeExtractionConfig,
    _JS_SCOPES,
    _PYTHON_SCOPES,
)

__all__ = [
    "PYTHON_PACK",
    "JAVASCRIPT_PACK",
    "TYPESCRIPT_PACK",
    "TSX_PACK",
]

# PYTHON

_PYTHON_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_definition
            name: (identifier) @name
            parameters: (parameters) @params) @node
        (class_definition
            name: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="class"),
    ),
    container_types=frozenset({"class_definition"}),
    body_node_types=frozenset({"block"}),
)

_PYTHON_SEM = """
    (call function: (identifier) @sem_call)
    (call function: (attribute attribute: (identifier) @sem_call))
    (assignment left: (attribute attribute: (identifier) @sem_field))
    (return_statement (identifier) @sem_return)
    (raise_statement (call function: (identifier) @sem_raise))
    (raise_statement (identifier) @sem_raise)
    (pair key: (string) @sem_key)
"""

_PYTHON_TYPES = TypeExtractionConfig(
    language_family="python",
    scope_node_types=("function_definition", "class_definition"),
    member_access_types=("attribute",),
    optional_patterns=("Optional[", "| None", "None |"),
    array_patterns=("list[", "List[", "Sequence[", "tuple[", "Tuple[", "set[", "Set["),
    generic_indicator="[",
    supports_interfaces=False,
    type_annotation_query="""
(typed_parameter
  (identifier) @name
  type: (type) @type) @param
(typed_default_parameter
  name: (identifier) @name
  type: (type) @type) @param
(function_definition
  name: (identifier) @name
  return_type: (type) @type) @return
(assignment
  left: (identifier) @name
  type: (type) @type)
""",
    type_member_query="""
(class_definition
  name: (identifier) @parent
  body: (block
    (function_definition
      name: (identifier) @member) @method))
(class_definition
  name: (identifier) @parent
  body: (block
    (expression_statement
      (assignment
        left: (identifier) @member
        type: (type) @type))))
""",
    member_access_query="""
(attribute
  object: (identifier) @receiver
  attribute: (identifier) @member) @expr
(call
  function: (attribute
    object: (identifier) @receiver
    attribute: (identifier) @member) @expr) @call
""",
    interface_impl_query="""
(class_definition
  name: (identifier) @implementor
  superclasses: (argument_list
    (identifier) @interface))
(class_definition
  name: (identifier) @implementor
  superclasses: (argument_list
    (attribute) @interface))
""",
)

PYTHON_PACK = LanguagePack(
    name="python",
    grammar_name="python",
    grammar_package="tree-sitter-python",
    grammar_module="tree_sitter_python",
    min_version="0.23.0",
    extensions=frozenset({"py", "pyi", "pyw", "pyx", "pxd"}),
    symbol_config=_PYTHON_SYMBOLS,
    scope_types=_PYTHON_SCOPES,
    sem_query=_PYTHON_SEM,
    type_config=_PYTHON_TYPES,
    import_query_config=ImportQueryConfig(
        query="""
; import X
(import_statement name: (dotted_name) @source) @node
; import X as Y
(import_statement name: (aliased_import name: (dotted_name) @source alias: (identifier) @alias)) @node
; from X import Y
(import_from_statement module_name: (dotted_name) @source name: (dotted_name) @name) @node
; from X import Y as Z
(import_from_statement module_name: (dotted_name) @source name: (aliased_import name: (dotted_name) @name alias: (identifier) @alias)) @node
; from .X import Y
(import_from_statement module_name: (relative_import) @source name: (dotted_name) @name) @node
; from .X import Y as Z
(import_from_statement module_name: (relative_import) @source name: (aliased_import name: (dotted_name) @name alias: (identifier) @alias)) @node
; from X import *
(import_from_statement module_name: (dotted_name) @source (wildcard_import) @name) @node
; from .X import *
(import_from_statement module_name: (relative_import) @source (wildcard_import) @name) @node
""",
        pattern_kinds={
            0: "python_import",
            1: "python_import",
            2: "python_from",
            3: "python_from",
            4: "python_from",
            5: "python_from",
            6: "python_from",
            7: "python_from",
        },
    ),
    dynamic_query="""
        (call) @dynamic_node
        (subscript) @dynamic_node
    """,
    dynamic_handler="_extract_python_dynamic",
)

# JAVASCRIPT

_JAVASCRIPT_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (generator_function_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (class_declaration
            name: (identifier) @name) @node
        (method_definition
            name: (property_identifier) @name
            parameters: (formal_parameters) @params) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="method"),
    ),
    container_types=frozenset({"class_declaration"}),
)

_JS_SEM = """
    (call_expression function: (identifier) @sem_call)
    (call_expression
        function: (member_expression
            property: (property_identifier) @sem_call))
    (assignment_expression
        left: (member_expression
            property: (property_identifier) @sem_field))
    (return_statement (identifier) @sem_return)
    (throw_statement
        (new_expression constructor: (identifier) @sem_raise))
    (pair key: (property_identifier) @sem_key)
"""

_JS_TS_TYPES = TypeExtractionConfig(
    language_family="javascript",
    scope_node_types=(
        "function_declaration",
        "method_definition",
        "class_declaration",
        "arrow_function",
    ),
    member_access_types=("member_expression",),
    optional_patterns=("| null", "| undefined", "?"),
    array_patterns=("[]", "Array<", "ReadonlyArray<"),
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
(required_parameter
  pattern: (identifier) @name
  type: (type_annotation (_) @type)) @param
(optional_parameter
  pattern: (identifier) @name
  type: (type_annotation (_) @type)) @param
(function_declaration
  name: (identifier) @name
  return_type: (type_annotation (_) @type)) @return
(method_definition
  name: (property_identifier) @name
  return_type: (type_annotation (_) @type)) @return
(arrow_function
  return_type: (type_annotation (_) @type)) @return
(variable_declarator
  name: (identifier) @name
  type: (type_annotation (_) @type))
(public_field_definition
  name: (property_identifier) @name
  type: (type_annotation (_) @type)) @field
(property_signature
  name: (property_identifier) @name
  type: (type_annotation (_) @type)) @field
""",
    type_member_query="""
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (method_definition
      name: (property_identifier) @member) @method))
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (public_field_definition
      name: (property_identifier) @member
      type: (type_annotation (_) @type)?)))
(interface_declaration
  name: (type_identifier) @parent
  body: (interface_body
    (method_signature
      name: (property_identifier) @member) @method))
(interface_declaration
  name: (type_identifier) @parent
  body: (interface_body
    (property_signature
      name: (property_identifier) @member
      type: (type_annotation (_) @type)?)))
""",
    member_access_query="""
(member_expression
  object: (identifier) @receiver
  property: (property_identifier) @member) @expr
(call_expression
  function: (member_expression
    object: (identifier) @receiver
    property: (property_identifier) @member) @expr) @call
""",
    interface_impl_query="""
(class_declaration
  name: (type_identifier) @implementor
  (class_heritage
    (implements_clause
      (type_identifier) @interface)))
""",
)

# Shared import/re-export query config for JS, TS, and TSX.
# Patterns 0-4: regular imports.  Patterns 5-8: re-exports.
_JS_IMPORT_QUERY = """
(import_statement (import_clause (identifier) @name) source: (string) @source) @node
(import_statement (import_clause (named_imports (import_specifier !alias name: (identifier) @name))) source: (string) @source) @node
(import_statement (import_clause (named_imports (import_specifier name: (identifier) @name alias: (identifier) @alias))) source: (string) @source) @node
(import_statement (import_clause (namespace_import (identifier) @alias)) source: (string) @source) @node
(variable_declarator name: (identifier) @name value: (call_expression function: (identifier) @_fn arguments: (arguments (string) @source) (#eq? @_fn "require"))) @node
(export_statement "*" source: (string) @source) @node
(export_statement (export_clause (export_specifier !alias name: (identifier) @name)) source: (string) @source) @node
(export_statement (export_clause (export_specifier name: (identifier) @name alias: (identifier) @alias)) source: (string) @source) @node
(export_statement (namespace_export (identifier) @alias) source: (string) @source) @node
"""

_JS_IMPORT_KINDS: dict[int, str] = {
    0: "js_import",
    1: "js_import",
    2: "js_import",
    3: "js_import",
    4: "js_require",
    5: "js_reexport",  # export * from './foo'
    6: "js_reexport",  # export { X } from './foo'
    7: "js_reexport",  # export { X as Y } from './foo'
    8: "js_reexport",  # export * as Y from './foo'
}

_JS_IMPORT_NAME_OVERRIDES: dict[int, str] = {
    3: "*",  # namespace import: import * as X
    5: "*",  # wildcard re-export: export * from './foo'
    8: "*",  # namespace re-export: export * as Y from './foo'
}

JAVASCRIPT_PACK = LanguagePack(
    name="javascript",
    grammar_name="javascript",
    grammar_package="tree-sitter-javascript",
    grammar_module="tree_sitter_javascript",
    min_version="0.23.0",
    extensions=frozenset({"js", "jsx", "mjs", "cjs"}),
    symbol_config=_JAVASCRIPT_SYMBOLS,
    scope_types=_JS_SCOPES,
    sem_query=_JS_SEM,
    type_config=_JS_TS_TYPES,
    import_query_config=ImportQueryConfig(
        query=_JS_IMPORT_QUERY,
        pattern_kinds=_JS_IMPORT_KINDS,
        name_overrides=_JS_IMPORT_NAME_OVERRIDES,
        strip_quotes=True,
    ),
    dynamic_query="""
        (subscript_expression) @dynamic_node
        (call_expression) @dynamic_node
    """,
    dynamic_handler="_extract_js_dynamic",
)

# TYPESCRIPT

_TYPESCRIPT_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (generator_function_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (class_declaration
            name: (type_identifier) @name) @node
        (method_definition
            name: (property_identifier) @name
            parameters: (formal_parameters) @params) @node
        (interface_declaration
            name: (type_identifier) @name) @node
        (type_alias_declaration
            name: (type_identifier) @name) @node
        (enum_declaration
            name: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="interface"),
        SymbolPattern(kind="type_alias"),
        SymbolPattern(kind="enum"),
    ),
    container_types=frozenset({"class_declaration"}),
)

_TS_SEM = """
    (call_expression function: (identifier) @sem_call)
    (call_expression
        function: (member_expression
            property: (property_identifier) @sem_call))
    (assignment_expression
        left: (member_expression
            property: (property_identifier) @sem_field))
    (return_statement (identifier) @sem_return)
    (throw_statement
        (new_expression constructor: (identifier) @sem_raise))
    (pair key: (property_identifier) @sem_key)
"""

TYPESCRIPT_PACK = LanguagePack(
    name="typescript",
    grammar_name="typescript",
    grammar_package="tree-sitter-typescript",
    grammar_module="tree_sitter_typescript",
    min_version="0.23.0",
    language_func="language_typescript",
    extensions=frozenset({"ts", "mts", "cts"}),
    symbol_config=_TYPESCRIPT_SYMBOLS,
    scope_types=_JS_SCOPES,
    sem_query=_TS_SEM,
    type_config=_JS_TS_TYPES,
    import_query_config=ImportQueryConfig(
        query=_JS_IMPORT_QUERY,
        pattern_kinds=_JS_IMPORT_KINDS,
        name_overrides=_JS_IMPORT_NAME_OVERRIDES,
        strip_quotes=True,
    ),
    dynamic_query="""
        (subscript_expression) @dynamic_node
        (call_expression) @dynamic_node
    """,
    dynamic_handler="_extract_js_dynamic",
)

TSX_PACK = LanguagePack(
    name="tsx",
    grammar_name="tsx",
    grammar_package="tree-sitter-typescript",
    grammar_module="tree_sitter_typescript",
    min_version="0.23.0",
    language_func="language_tsx",
    extensions=frozenset({"tsx"}),
    symbol_config=_TYPESCRIPT_SYMBOLS,
    scope_types=_JS_SCOPES,
    sem_query=_TS_SEM,
    type_config=_JS_TS_TYPES,
    import_query_config=ImportQueryConfig(
        query=_JS_IMPORT_QUERY,
        pattern_kinds=_JS_IMPORT_KINDS,
        name_overrides=_JS_IMPORT_NAME_OVERRIDES,
        strip_quotes=True,
    ),
    dynamic_query="""
        (subscript_expression) @dynamic_node
        (call_expression) @dynamic_node
    """,
    dynamic_handler="_extract_js_dynamic",
)
