"""Language packs for JVM/.NET languages: Java, C#, Kotlin."""

from __future__ import annotations

from coderecon.index._internal.parsing.packs_base import (
    _CSHARP_SCOPES,
    _JAVA_SCOPES,
    _KOTLIN_SCOPES,
    ImportQueryConfig,
    LanguagePack,
    SymbolPattern,
    SymbolQueryConfig,
    TypeExtractionConfig,
)

__all__ = [
    "JAVA_PACK",
    "CSHARP_PACK",
    "KOTLIN_PACK",
]

# JAVA

_JAVA_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (class_declaration
            name: (identifier) @name) @node
        (interface_declaration
            name: (identifier) @name) @node
        (enum_declaration
            name: (identifier) @name) @node
        (record_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (method_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (constructor_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (annotation_type_declaration
            name: (identifier) @name) @node
        (enum_constant
            name: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="class"),
        SymbolPattern(kind="interface"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="record"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="constructor"),
        SymbolPattern(kind="annotation"),
        SymbolPattern(kind="enum_constant"),
    ),
    container_types=frozenset({"class_declaration", "interface_declaration", "enum_declaration"}),
)

_JAVA_SEM = """
    (method_invocation name: (identifier) @sem_call)
    (assignment_expression
        left: (field_access
            field: (identifier) @sem_field))
    (return_statement (identifier) @sem_return)
    (throw_statement
        (object_creation_expression
            type: (type_identifier) @sem_raise))
"""

_JAVA_TYPES = TypeExtractionConfig(
    language_family="jvm",
    scope_node_types=("method_declaration", "constructor_declaration", "class_declaration"),
    member_access_types=("field_access", "method_invocation"),
    optional_patterns=("Optional<",),
    array_patterns=("[]", "List<", "Set<", "Collection<"),
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
(formal_parameter
  type: (_) @type
  name: (identifier) @name) @param
(method_declaration
  type: (_) @type
  name: (identifier) @name) @return
(field_declaration
  type: (_) @type
  declarator: (variable_declarator
    name: (identifier) @name)) @field
(local_variable_declaration
  type: (_) @type
  declarator: (variable_declarator
    name: (identifier) @name))
""",
    type_member_query="""
(class_declaration
  name: (identifier) @parent
  body: (class_body
    (method_declaration
      name: (identifier) @member) @method))
(class_declaration
  name: (identifier) @parent
  body: (class_body
    (field_declaration
      declarator: (variable_declarator
        name: (identifier) @member))))
(interface_declaration
  name: (identifier) @parent
  body: (interface_body
    (method_declaration
      name: (identifier) @member) @method))
(enum_declaration
  name: (identifier) @parent
  body: (enum_body
    (enum_constant
      name: (identifier) @member)))
""",
    member_access_query="""
(field_access
  object: (identifier) @receiver
  field: (identifier) @member) @expr
(method_invocation
  object: (identifier) @receiver
  name: (identifier) @member
  arguments: (argument_list) @args) @call
""",
    interface_impl_query="""
(class_declaration
  name: (identifier) @implementor
  interfaces: (super_interfaces
    (type_list
      (_) @interface)))
""",
)

JAVA_PACK = LanguagePack(
    name="java",
    grammar_name="java",
    grammar_package="tree-sitter-java",
    grammar_module="tree_sitter_java",
    min_version="0.23.0",
    extensions=frozenset({"java"}),
    symbol_config=_JAVA_SYMBOLS,
    scope_types=_JAVA_SCOPES,
    sem_query=_JAVA_SEM,
    type_config=_JAVA_TYPES,
    import_query_config=ImportQueryConfig(
        query="""
; import x.y.*;
(import_declaration (scoped_identifier) @source (asterisk)) @node
; import x.y.z;
(import_declaration (scoped_identifier) @source) @node
""",
        pattern_kinds={0: "java_import", 1: "java_import"},
        name_overrides={0: "*"},  # wildcard import
        name_from_source_segment=True,
        source_segment_sep=".",
    ),
    declared_module_query="(package_declaration) @module_node",
    declared_module_handler="_declared_module_java",
)

# C#

_CSHARP_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (class_declaration
            name: (identifier) @name) @node
        (interface_declaration
            name: (identifier) @name) @node
        (struct_declaration
            name: (identifier) @name) @node
        (enum_declaration
            name: (identifier) @name) @node
        (record_declaration
            name: (identifier) @name) @node
        (method_declaration
            name: (identifier) @name
            parameters: (parameter_list) @params) @node
        (constructor_declaration
            name: (identifier) @name
            parameters: (parameter_list) @params) @node
        (property_declaration
            name: (identifier) @name) @node
        (field_declaration
            (variable_declaration
                (variable_declarator
                    (identifier) @name))) @node
        (namespace_declaration
            name: (_) @name) @node
        (delegate_declaration
            name: (identifier) @name
            parameters: (parameter_list) @params) @node
        (event_declaration
            name: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="class"),
        SymbolPattern(kind="interface"),
        SymbolPattern(kind="struct"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="record"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="constructor"),
        SymbolPattern(kind="property"),
        SymbolPattern(kind="field"),
        SymbolPattern(kind="namespace"),
        SymbolPattern(kind="delegate"),
        SymbolPattern(kind="event"),
    ),
    container_types=frozenset(
        {
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "record_declaration",
        }
    ),
)

_CSHARP_TYPES = TypeExtractionConfig(
    language_family="dotnet",
    scope_node_types=("method_declaration", "constructor_declaration", "class_declaration"),
    member_access_types=("member_access_expression",),
    optional_patterns=("?",),
    array_patterns=("[]", "List<", "IEnumerable<", "ICollection<"),
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
(parameter
  type: (_) @type
  name: (identifier) @name) @param
(method_declaration
  returns: (_) @type
  name: (identifier) @name) @return
(property_declaration
  type: (_) @type
  name: (identifier) @name) @field
(field_declaration
  (variable_declaration
    type: (_) @type
    (variable_declarator
      (identifier) @name))) @field
(local_declaration_statement
  (variable_declaration
    type: (_) @type
    (variable_declarator
      (identifier) @name)))
""",
    type_member_query="""
(class_declaration
  name: (identifier) @parent
  body: (declaration_list
    (method_declaration
      name: (identifier) @member) @method))
(class_declaration
  name: (identifier) @parent
  body: (declaration_list
    (property_declaration
      name: (identifier) @member)))
(class_declaration
  name: (identifier) @parent
  body: (declaration_list
    (field_declaration
      (variable_declaration
        (variable_declarator
          (identifier) @member)))))
(interface_declaration
  name: (identifier) @parent
  body: (declaration_list
    (method_declaration
      name: (identifier) @member) @method))
""",
    member_access_query="""
(member_access_expression
  expression: (identifier) @receiver
  name: (identifier) @member) @expr
(invocation_expression
  function: (member_access_expression
    expression: (identifier) @receiver
    name: (identifier) @member) @expr
  arguments: (argument_list) @args) @call
""",
    interface_impl_query="""
(class_declaration
  name: (identifier) @implementor
  (base_list
    (_) @interface))
(struct_declaration
  name: (identifier) @implementor
  (base_list
    (_) @interface))
""",
)

CSHARP_PACK = LanguagePack(
    name="csharp",
    grammar_name="c_sharp",
    grammar_package="tree-sitter-c-sharp",
    grammar_module="tree_sitter_c_sharp",
    min_version="0.23.0",
    extensions=frozenset({"cs"}),
    symbol_config=_CSHARP_SYMBOLS,
    scope_types=_CSHARP_SCOPES,
    type_config=_CSHARP_TYPES,
    sem_query="""
    (invocation_expression function: (identifier) @sem_call)
    (invocation_expression function: (member_access_expression name: (identifier) @sem_call))
    (assignment_expression left: (member_access_expression name: (identifier) @sem_field))
    (return_statement (identifier) @sem_return)
    (throw_statement (object_creation_expression type: (identifier) @sem_raise))
""",
    import_query_config=ImportQueryConfig(
        query="""
(using_directive) @node
""",
        pattern_kinds={0: "csharp_using"},
        source_from_node_text=True,
    ),
    declared_module_handler="_declared_module_csharp",
)

# KOTLIN

_KOTLIN_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_declaration
            (identifier) @name
            (function_value_parameters) @params) @node
        (class_declaration
            (identifier) @name) @node
        (object_declaration
            (identifier) @name) @node
        (property_declaration
            (variable_declaration
                (identifier) @name)) @node
        (companion_object) @node
        (enum_entry
            (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="object"),
        SymbolPattern(kind="property"),
        SymbolPattern(kind="companion_object"),
        SymbolPattern(kind="enum_constant"),
    ),
    container_types=frozenset({"class_declaration", "object_declaration"}),
)

_KOTLIN_TYPES = TypeExtractionConfig(
    language_family="jvm",
    scope_node_types=("function_declaration", "class_declaration"),
    member_access_types=("navigation_expression",),
    optional_patterns=("?",),
    array_patterns=("List<", "Array<", "Set<", "Collection<"),
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
(parameter
  (simple_identifier) @name
  (user_type) @type) @param
(function_declaration
  (simple_identifier) @name
  (user_type) @type) @return
(property_declaration
  (variable_declaration
    (simple_identifier) @name
    (user_type) @type?))
""",
    type_member_query="""
(class_declaration
  (type_identifier) @parent
  (class_body
    (function_declaration
      (simple_identifier) @member) @method))
(class_declaration
  (type_identifier) @parent
  (class_body
    (property_declaration
      (variable_declaration
        (simple_identifier) @member))))
(interface_declaration
  (type_identifier) @parent
  (interface_body
    (function_declaration
      (simple_identifier) @member) @method))
""",
    member_access_query="""
(navigation_expression
  (simple_identifier) @receiver
  (navigation_suffix
    (simple_identifier) @member)) @expr
(call_expression
  (navigation_expression
    (simple_identifier) @receiver
    (navigation_suffix
      (simple_identifier) @member)) @expr) @call
""",
    interface_impl_query="""
(class_declaration
  (type_identifier) @implementor
  (delegation_specifiers
    (delegation_specifier
      (user_type
        (type_identifier) @interface))))
""",
)

KOTLIN_PACK = LanguagePack(
    name="kotlin",
    grammar_name="kotlin",
    grammar_package="tree-sitter-kotlin",
    grammar_module="tree_sitter_kotlin",
    min_version="1.0.0",
    extensions=frozenset({"kt", "kts"}),
    symbol_config=_KOTLIN_SYMBOLS,
    scope_types=_KOTLIN_SCOPES,
    type_config=_KOTLIN_TYPES,
    sem_query="""
    (call_expression (identifier) @sem_call)
    (call_expression (navigation_expression (identifier) @sem_call))
    (return_expression (identifier) @sem_return)
    (throw_expression (call_expression (identifier) @sem_raise))
""",
    import_query_config=ImportQueryConfig(
        query="""
(import (qualified_identifier) @source (identifier) @alias) @node
(import (qualified_identifier) @source "*") @node
(import (qualified_identifier) @source) @node
""",
        pattern_kinds={0: "kotlin_import", 1: "kotlin_import", 2: "kotlin_import"},
        name_overrides={1: "*"},  # wildcard import
    ),
    declared_module_query="(package_header) @module_node",
    declared_module_handler="_declared_module_kotlin",
)
