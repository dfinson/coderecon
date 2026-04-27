"""Language packs for native/C-family languages: C, C++."""

from __future__ import annotations

from coderecon.index._internal.parsing.packs_base import (
    _C_CPP_SCOPES,
    ImportQueryConfig,
    LanguagePack,
    SymbolPattern,
    SymbolQueryConfig,
    TypeExtractionConfig,
)

__all__ = [
    "CPP_PACK",
    "C_PACK",
]

# C / C++

# C-only symbol query: no qualified_identifier, namespace_definition, or
# class_specifier (those are C++-only AST node types).  Sharing the C++
# query caused tree-sitter v0.25 to poison its internal query cache when
# the C grammar rejected the C++-only nodes, which then broke *all*
# subsequent C++ query compilations in the same process.
_C_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_definition
            declarator: (function_declarator
                declarator: (identifier) @name
                parameters: (parameter_list) @params)) @node
        (function_definition
            declarator: (function_declarator
                declarator: (field_identifier) @name
                parameters: (parameter_list) @params)) @node
        (struct_specifier
            name: (type_identifier) @name) @node
        (enum_specifier
            name: (type_identifier) @name) @node
        (declaration
            declarator: (function_declarator
                declarator: (identifier) @name
                parameters: (parameter_list) @params)) @node
        (field_declaration
            declarator: (function_declarator
                declarator: (identifier) @name
                parameters: (parameter_list) @params)) @node
        (field_declaration
            declarator: (function_declarator
                declarator: (field_identifier) @name
                parameters: (parameter_list) @params)) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="struct"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="method"),
    ),
    container_types=frozenset({"struct_specifier"}),
)

# C-only type extraction config: no qualified_identifier references
_C_TYPES = TypeExtractionConfig(
    language_family="c",
    scope_node_types=("function_definition", "struct_specifier"),
    member_access_types=("field_expression",),
    member_identifier_types=("field_identifier",),
    access_styles=("dot", "arrow"),
    optional_patterns=(),
    array_patterns=("[]",),
    generic_indicator="",
    reference_indicator="",
    supports_interfaces=False,
    type_annotation_query="""
(parameter_declaration
  type: (_) @type
  declarator: (identifier) @name) @param
(parameter_declaration
  type: (_) @type
  declarator: (pointer_declarator
    declarator: (identifier) @name)) @param
(function_definition
  type: (_) @type
  declarator: (function_declarator
    declarator: (identifier) @name)) @return
(declaration
  type: (_) @type
  declarator: (init_declarator
    declarator: (identifier) @name))
""",
    type_member_query="""
(struct_specifier
  name: (type_identifier) @parent
  body: (field_declaration_list
    (field_declaration
      type: (_) @type
      declarator: (field_identifier) @member)))
""",
    member_access_query="""
(field_expression
  argument: (identifier) @receiver
  field: (field_identifier) @member) @expr
(call_expression
  function: (field_expression
    argument: (identifier) @receiver
    field: (field_identifier) @member) @expr
  arguments: (argument_list) @args) @call
""",
    interface_impl_query=None,
)

_CPP_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_definition
            declarator: (function_declarator
                declarator: (identifier) @name
                parameters: (parameter_list) @params)) @node
        (function_definition
            declarator: (function_declarator
                declarator: (qualified_identifier
                    name: (_) @name)
                parameters: (parameter_list) @params)) @node
        (function_definition
            declarator: (function_declarator
                declarator: (field_identifier) @name
                parameters: (parameter_list) @params)) @node
        (class_specifier
            name: (type_identifier) @name) @node
        (struct_specifier
            name: (type_identifier) @name) @node
        (namespace_definition
            name: (namespace_identifier) @name) @node
        (enum_specifier
            name: (type_identifier) @name) @node
        (declaration
            declarator: (function_declarator
                declarator: (identifier) @name
                parameters: (parameter_list) @params)) @node
        (declaration
            declarator: (function_declarator
                declarator: (qualified_identifier
                    name: (_) @name)
                parameters: (parameter_list) @params)) @node
        (field_declaration
            declarator: (function_declarator
                declarator: (identifier) @name
                parameters: (parameter_list) @params)) @node
        (field_declaration
            declarator: (function_declarator
                declarator: (qualified_identifier
                    name: (_) @name)
                parameters: (parameter_list) @params)) @node
        (field_declaration
            declarator: (function_declarator
                declarator: (field_identifier) @name
                parameters: (parameter_list) @params)) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="struct"),
        SymbolPattern(kind="namespace"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="method"),
    ),
    container_types=frozenset({"class_specifier", "struct_specifier", "namespace_definition"}),
)

_CPP_TYPES = TypeExtractionConfig(
    language_family="cpp",
    scope_node_types=("function_definition", "class_specifier", "struct_specifier"),
    member_access_types=("field_expression",),
    member_identifier_types=("field_identifier",),
    access_styles=("dot", "arrow", "scope"),
    optional_patterns=(),
    array_patterns=("[]", "vector<", "array<"),
    generic_indicator="<",
    reference_indicator="&",
    supports_interfaces=True,
    type_annotation_query="""
(parameter_declaration
  type: (_) @type
  declarator: (identifier) @name) @param
(parameter_declaration
  type: (_) @type
  declarator: (pointer_declarator
    declarator: (identifier) @name)) @param
(function_definition
  type: (_) @type
  declarator: (function_declarator
    declarator: (identifier) @name)) @return
(declaration
  type: (_) @type
  declarator: (init_declarator
    declarator: (identifier) @name))
""",
    type_member_query="""
(struct_specifier
  name: (type_identifier) @parent
  body: (field_declaration_list
    (field_declaration
      type: (_) @type
      declarator: (field_identifier) @member)))
(class_specifier
  name: (type_identifier) @parent
  body: (field_declaration_list
    (field_declaration
      type: (_) @type
      declarator: (field_identifier) @member)))
(class_specifier
  name: (type_identifier) @parent
  body: (field_declaration_list
    (function_definition
      declarator: (function_declarator
        declarator: (identifier) @member)) @method))
""",
    member_access_query="""
(field_expression
  argument: (identifier) @receiver
  field: (field_identifier) @member) @expr
(qualified_identifier
  scope: (namespace_identifier) @receiver
  name: (identifier) @member) @expr @scope
(call_expression
  function: (field_expression
    argument: (identifier) @receiver
    field: (field_identifier) @member) @expr
  arguments: (argument_list) @args) @call
""",
    interface_impl_query="""
(class_specifier
  name: (type_identifier) @implementor
  (base_class_clause
    (type_identifier) @interface))
""",
)

CPP_PACK = LanguagePack(
    name="cpp",
    grammar_name="cpp",
    grammar_package="tree-sitter-cpp",
    grammar_module="tree_sitter_cpp",
    min_version="0.23.0",
    extensions=frozenset({"cpp", "cc", "cxx", "hpp", "hxx", "hh", "h"}),
    symbol_config=_CPP_SYMBOLS,
    scope_types=_C_CPP_SCOPES,
    sem_query="""
    (call_expression function: (identifier) @sem_call)
    (call_expression function: (field_expression field: (field_identifier) @sem_call))
    (call_expression function: (qualified_identifier name: (identifier) @sem_call))
    (assignment_expression left: (field_expression field: (field_identifier) @sem_field))
    (return_statement (identifier) @sem_return)
    (throw_statement (call_expression function: (identifier) @sem_raise))
""",
    type_config=_CPP_TYPES,
    import_query_config=ImportQueryConfig(
        query="""
(preproc_include path: (system_lib_string) @source) @node
(preproc_include path: (string_literal) @source) @node
""",
        pattern_kinds={0: "c_include", 1: "c_include"},
        strip_quotes=True,
        strip_angle_brackets=True,
    ),
)

C_PACK = LanguagePack(
    name="c",
    grammar_name="c",
    grammar_package="tree-sitter-c",
    grammar_module="tree_sitter_c",
    min_version="0.23.0",
    extensions=frozenset({"c"}),
    symbol_config=_C_SYMBOLS,
    scope_types=_C_CPP_SCOPES,
    type_config=_C_TYPES,
    sem_query="""
    (call_expression function: (identifier) @sem_call)
    (call_expression function: (field_expression field: (field_identifier) @sem_call))
    (assignment_expression left: (field_expression field: (field_identifier) @sem_field))
    (return_statement (identifier) @sem_return)
""",
    import_query_config=ImportQueryConfig(
        query="""
(preproc_include path: (system_lib_string) @source) @node
(preproc_include path: (string_literal) @source) @node
""",
        pattern_kinds={0: "c_include", 1: "c_include"},
        strip_quotes=True,
        strip_angle_brackets=True,
    ),
)
