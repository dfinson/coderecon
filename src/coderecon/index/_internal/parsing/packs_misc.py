"""Language packs for miscellaneous languages: Zig, Julia, Lua, Bash."""

from __future__ import annotations

from coderecon.index._internal.parsing.packs_base import (
    _JULIA_SCOPES,
    _LUA_SCOPES,
    ImportQueryConfig,
    LanguagePack,
    SymbolPattern,
    SymbolQueryConfig,
    TypeExtractionConfig,
)

__all__ = [
    "ZIG_PACK",
    "JULIA_PACK",
    "LUA_PACK",
    "BASH_PACK",
]

# ZIG

_ZIG_TYPES = TypeExtractionConfig(
    language_family="zig",
    scope_node_types=("fn_decl",),
    member_access_types=("field_access",),
    optional_patterns=("?",),
    array_patterns=("[]",),
    supports_type_annotations=True,
    type_annotation_query="""
(param_decl
  name: (identifier) @name
  type: (_) @type) @param
(fn_decl
  name: (identifier) @name
  return_type: (_) @type) @return
(var_decl
  name: (identifier) @name
  type: (_) @type)
(const_decl
  name: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
(container_decl
  (container_field
    name: (identifier) @member
    type: (_) @type))
""",
    member_access_query="""
(field_access
  operand: (identifier) @receiver
  field: (identifier) @member) @expr
(call_expr
  function: (field_access
    operand: (identifier) @receiver
    field: (identifier) @member) @expr) @call
""",
)

ZIG_PACK = LanguagePack(
    name="zig",
    grammar_name="zig",
    grammar_package="tree-sitter-zig",
    grammar_module="tree_sitter_zig",
    min_version="1.1.0",
    extensions=frozenset({"zig"}),
    symbol_config=SymbolQueryConfig(
        query_text="""
        (function_declaration
            (identifier) @name) @node
        (variable_declaration
            (identifier) @name) @node
    """,
        patterns=(
            SymbolPattern(kind="function"),
            SymbolPattern(kind="variable"),
        ),
        container_types=frozenset({"struct"}),
    ),
    sem_query="""
    (call_expression (identifier) @sem_call)
""",
    scope_types={
        "function_declaration": "function",
        "container_declaration": "class",
        "for_statement": "block",
        "while_statement": "block",
        "if_statement": "block",
        "block": "block",
    },
    type_config=_ZIG_TYPES,
)

# JULIA

_JULIA_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_definition
            (signature
                (typed_expression
                    (call_expression
                        (identifier) @name
                        (argument_list) @params)))) @node
        (function_definition
            (signature
                (call_expression
                    (identifier) @name
                    (argument_list) @params))) @node
        (assignment
            (call_expression
                (identifier) @name
                (argument_list) @params)) @node
        (macro_definition
            (signature
                (call_expression
                    (identifier) @name
                    (argument_list) @params))) @node
        (struct_definition
            (type_head) @name) @node
        (module_definition
            name: (identifier) @name) @node
        (abstract_definition
            (type_head) @name) @node
        (const_statement
            (assignment
                (identifier) @name)) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="macro"),
        SymbolPattern(kind="struct"),
        SymbolPattern(kind="module"),
        SymbolPattern(kind="abstract_type"),
        SymbolPattern(kind="constant"),
    ),
)

JULIA_PACK = LanguagePack(
    name="julia",
    grammar_name="julia",
    grammar_package="tree-sitter-julia",
    grammar_module="tree_sitter_julia",
    min_version="0.23.0",
    extensions=frozenset({"jl"}),
    symbol_config=_JULIA_SYMBOLS,
    scope_types=_JULIA_SCOPES,
    sem_query="""
    (call_expression (identifier) @sem_call)
    (call_expression (field_expression (identifier) @sem_call))
    (return_statement (identifier) @sem_return)
""",
    import_query_config=ImportQueryConfig(
        query="""
(import_statement (identifier) @source) @node
(import_statement (selected_import (identifier) @source)) @node
(using_statement (identifier) @source) @node
(using_statement (selected_import (identifier) @source)) @node
""",
        pattern_kinds={0: "julia_import", 1: "julia_import", 2: "julia_using", 3: "julia_using"},
    ),
    declared_module_query="(module_definition) @module_node",
    declared_module_handler="_declared_module_julia",
)

# LUA

_LUA_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_declaration
            name: (_) @name
            parameters: (parameters) @params) @node
        (variable_declaration
            (assignment_statement
                (variable_list
                    (identifier) @name))) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="variable"),
    ),
)

LUA_PACK = LanguagePack(
    name="lua",
    grammar_name="lua",
    grammar_package="tree-sitter-lua",
    grammar_module="tree_sitter_lua",
    min_version="0.2.0",
    extensions=frozenset({"lua"}),
    symbol_config=_LUA_SYMBOLS,
    scope_types=_LUA_SCOPES,
    sem_query="""
    (function_call (identifier) @sem_call)
    (function_call (dot_index_expression field: (identifier) @sem_call))
    (return_statement (expression_list (identifier) @sem_return))
""",
    import_query_config=ImportQueryConfig(
        query="""
(function_call (identifier) @_fn (arguments (string) @source) (#match? @_fn "^(require|dofile|loadfile)$")) @node
(variable_declaration (assignment_statement (variable_list (identifier) @name) (expression_list (function_call (identifier) @_fn (arguments (string) @source) (#match? @_fn "^require$"))))) @node
""",
        pattern_kinds={0: "lua_require", 1: "lua_require"},
        strip_quotes=True,
        kind_from_capture="_fn",
        kind_from_capture_map={
            "require": "lua_require",
            "dofile": "lua_dofile",
            "loadfile": "lua_loadfile",
        },
    ),
)

# BASH

_BASH_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_definition
            name: (word) @name) @node
        (variable_assignment
            name: (variable_name) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="variable"),
    ),
)

BASH_PACK = LanguagePack(
    name="bash",
    grammar_name="bash",
    grammar_package="tree-sitter-bash",
    grammar_module="tree_sitter_bash",
    min_version="0.23.0",
    extensions=frozenset({"sh", "bash", "zsh"}),
    symbol_config=_BASH_SYMBOLS,
    scope_types={
        "function_definition": "function",
        "for_statement": "block",
        "while_statement": "block",
        "if_statement": "block",
        "case_statement": "block",
    },
)
