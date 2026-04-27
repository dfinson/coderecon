"""Language packs for scripting/dynamic languages: Ruby, PHP, Swift."""

from __future__ import annotations

from coderecon.index._internal.parsing.packs_base import (
    ImportQueryConfig,
    LanguagePack,
    SymbolPattern,
    SymbolQueryConfig,
    TypeExtractionConfig,
    _PHP_SCOPES,
    _RUBY_SCOPES,
    _SWIFT_SCOPES,
)

__all__ = [
    "RUBY_PACK",
    "PHP_PACK",
    "SWIFT_PACK",
]

# RUBY

_RUBY_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (method
            name: (identifier) @name
            parameters: (method_parameters) @params) @node
        (singleton_method
            name: (identifier) @name
            parameters: (method_parameters) @params) @node
        (singleton_method
            name: (identifier) @name) @node
        (class
            name: (constant) @name) @node
        (module
            name: (constant) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="module"),
    ),
    container_types=frozenset({"class", "module"}),
)

_RUBY_TYPES = TypeExtractionConfig(
    language_family="ruby",
    scope_node_types=("method", "class"),
    member_access_types=("call",),
    supports_type_annotations=False,
    supports_interfaces=False,
    type_annotation_query="",
    type_member_query="""
(class
  name: (constant) @parent
  body: (body_statement
    (method
      name: (identifier) @member) @method)?)
(class
  name: (constant) @parent
  body: (body_statement
    (singleton_method
      name: (identifier) @member) @method @static)?)
(class
  name: (constant) @parent
  body: (body_statement
    (call
      method: (identifier) @kind
      arguments: (argument_list
        (simple_symbol) @member))))
""",
    member_access_query="""
(call
  receiver: (identifier) @receiver
  method: (identifier) @member) @expr
(call
  receiver: (identifier) @receiver
  method: (identifier) @member
  arguments: (argument_list) @args) @call
""",
    interface_impl_query="""
(class
  name: (constant) @implementor
  superclass: (superclass
    (constant) @interface))
(class
  name: (constant) @implementor
  superclass: (superclass
    (scope_resolution) @interface))
""",
)

RUBY_PACK = LanguagePack(
    name="ruby",
    grammar_name="ruby",
    grammar_package="tree-sitter-ruby",
    grammar_module="tree_sitter_ruby",
    min_version="0.23.0",
    extensions=frozenset({"rb", "rake"}),
    filenames=frozenset({"vagrantfile", "rakefile", "gemfile", "guardfile"}),
    symbol_config=_RUBY_SYMBOLS,
    scope_types=_RUBY_SCOPES,
    type_config=_RUBY_TYPES,
    sem_query="""
    (call method: (identifier) @sem_call)
    (return (argument_list (identifier) @sem_return))
""",
    import_query_config=ImportQueryConfig(
        query="""
(call method: (identifier) @_fn arguments: (argument_list (string) @source) (#match? @_fn "^(require|require_relative|load)$")) @node
""",
        pattern_kinds={0: "ruby_require"},
        strip_quotes=True,
        kind_from_capture="_fn",
        kind_from_capture_map={
            "require": "ruby_require",
            "require_relative": "ruby_require_relative",
            "load": "ruby_load",
        },
    ),
    declared_module_handler="_declared_module_ruby",
)

# PHP

_PHP_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_definition
            name: (name) @name
            parameters: (formal_parameters) @params) @node
        (class_declaration
            name: (name) @name) @node
        (interface_declaration
            name: (name) @name) @node
        (trait_declaration
            name: (name) @name) @node
        (method_declaration
            name: (name) @name
            parameters: (formal_parameters) @params) @node
        (property_declaration
            (property_element
                (variable_name
                    (name) @name))) @node
        (enum_declaration
            name: (name) @name) @node
        (enum_case
            name: (name) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="interface"),
        SymbolPattern(kind="trait"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="property"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="enum_case"),
    ),
    container_types=frozenset({"class_declaration", "interface_declaration", "trait_declaration"}),
)

_PHP_TYPES = TypeExtractionConfig(
    language_family="php",
    scope_node_types=("method_declaration", "function_definition", "class_declaration"),
    member_access_types=("member_access_expression",),
    access_styles=("arrow",),
    optional_patterns=("?",),
    array_patterns=("array", "iterable"),
    generic_indicator="",
    supports_interfaces=True,
    type_annotation_query="""
(simple_parameter
  type: (_) @type
  name: (variable_name) @name) @param
(function_definition
  name: (name) @name
  return_type: (union_type) @type) @return
(function_definition
  name: (name) @name
  return_type: (named_type) @type) @return
(method_declaration
  name: (name) @name
  return_type: (_) @type) @return
(property_declaration
  type: (_) @type
  (property_element
    (variable_name) @name)) @field
""",
    type_member_query="""
(class_declaration
  name: (name) @parent
  body: (declaration_list
    (method_declaration
      name: (name) @member) @method))
(class_declaration
  name: (name) @parent
  body: (declaration_list
    (property_declaration
      type: (_) @type?
      (property_element
        (variable_name) @member))))
(interface_declaration
  name: (name) @parent
  body: (declaration_list
    (method_declaration
      name: (name) @member) @method))
""",
    member_access_query="""
(member_access_expression
  object: (variable_name) @receiver
  name: (name) @member) @expr
(member_call_expression
  object: (variable_name) @receiver
  name: (name) @member
  arguments: (arguments) @args) @call
""",
    interface_impl_query="""
(class_declaration
  name: (name) @implementor
  (class_interface_clause
    (name) @interface))
""",
)

PHP_PACK = LanguagePack(
    name="php",
    grammar_name="php",
    grammar_package="tree-sitter-php",
    grammar_module="tree_sitter_php",
    min_version="0.23.0",
    language_func="language_php",
    extensions=frozenset({"php"}),
    symbol_config=_PHP_SYMBOLS,
    scope_types=_PHP_SCOPES,
    type_config=_PHP_TYPES,
    sem_query="""
    (function_call_expression function: (name) @sem_call)
    (member_call_expression name: (name) @sem_call)
    (assignment_expression left: (member_access_expression name: (name) @sem_field))
    (return_statement (name) @sem_return)
    (throw_expression (object_creation_expression (name) @sem_raise))
""",
    import_query_config=ImportQueryConfig(
        query="""
(namespace_use_declaration (namespace_use_clause (qualified_name) @source (name) @alias)) @node
(namespace_use_declaration (namespace_use_clause (qualified_name) @source)) @node
""",
        pattern_kinds={0: "php_use", 1: "php_use"},
    ),
    declared_module_query="(namespace_definition) @module_node",
    declared_module_handler="_declared_module_php",
)

# SWIFT

_SWIFT_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (class_declaration
            "class"
            name: (type_identifier) @name) @node
        (class_declaration
            "struct"
            name: (type_identifier) @name) @node
        (class_declaration
            "enum"
            name: (type_identifier) @name) @node
        (protocol_declaration
            name: (type_identifier) @name) @node
        (function_declaration
            name: (simple_identifier) @name) @node
        (protocol_function_declaration
            name: (simple_identifier) @name) @node
        (property_declaration
            name: (pattern
                (simple_identifier) @name)) @node
        (enum_entry
            name: (simple_identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="class"),
        SymbolPattern(kind="struct"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="protocol"),
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="property"),
        SymbolPattern(kind="enum_case"),
    ),
    container_types=frozenset({"class_declaration", "protocol_declaration"}),
    params_from_children=True,
)

_SWIFT_TYPES = TypeExtractionConfig(
    language_family="swift",
    scope_node_types=("function_declaration", "class_declaration"),
    member_access_types=("navigation_expression",),
    optional_patterns=("?",),
    array_patterns=("[", "Array<"),
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
(parameter
  (simple_identifier) @name
  (type_annotation
    (_) @type)) @param
(function_declaration
  name: (simple_identifier) @name
  (function_signature
    (return_clause
      (_) @type))) @return
(property_declaration
  (pattern
    (simple_identifier) @name)
  (type_annotation
    (_) @type))
(local_declaration
  (value_binding_pattern
    (pattern
      (simple_identifier) @name)
    (type_annotation
      (_) @type)))
""",
    type_member_query="""
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (function_declaration
      name: (simple_identifier) @member) @method))
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (property_declaration
      (pattern
        (simple_identifier) @member)
      (type_annotation
        (_) @type)?)))
(protocol_declaration
  name: (type_identifier) @parent
  body: (protocol_body
    (protocol_method_declaration
      name: (simple_identifier) @member) @method))
""",
    member_access_query="""
(navigation_expression
  target: (simple_identifier) @receiver
  suffix: (navigation_suffix
    (simple_identifier) @member)) @expr
(call_expression
  (navigation_expression
    target: (simple_identifier) @receiver
    suffix: (navigation_suffix
      (simple_identifier) @member)) @expr) @call
""",
    interface_impl_query="""
(class_declaration
  name: (type_identifier) @implementor
  (inheritance_clause
    (inheritance_specifier
      (type_identifier) @interface)))
""",
)

SWIFT_PACK = LanguagePack(
    name="swift",
    grammar_name="swift",
    grammar_package="tree-sitter-swift",
    grammar_module="tree_sitter_swift",
    min_version="0.0.1",
    extensions=frozenset({"swift"}),
    symbol_config=_SWIFT_SYMBOLS,
    scope_types=_SWIFT_SCOPES,
    type_config=_SWIFT_TYPES,
    sem_query="""
    (call_expression (simple_identifier) @sem_call)
    (call_expression (navigation_expression (navigation_suffix (simple_identifier) @sem_call)))
""",
    import_query_config=ImportQueryConfig(
        query="""
(import_declaration (identifier) @source) @node
""",
        pattern_kinds={0: "swift_import"},
    ),
)
