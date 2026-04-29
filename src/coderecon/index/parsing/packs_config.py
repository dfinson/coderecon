"""Language packs for data, config, build, and minimal grammar-only languages.

Includes: Ada, Fortran, Odin, HTML, CSS, XML, Verilog, JSON, YAML, TOML,
HCL, SQL, GraphQL, Markdown, Makefile, Dockerfile, Regex, Requirements.
"""

from __future__ import annotations

from coderecon.index.parsing.packs_base import (
    LanguagePack,
    SymbolPattern,
    SymbolQueryConfig,
)

__all__ = [
    "ADA_PACK",
    "FORTRAN_PACK",
    "ODIN_PACK",
    "HTML_PACK",
    "CSS_PACK",
    "XML_PACK",
    "VERILOG_PACK",
    "JSON_PACK",
    "YAML_PACK",
    "TOML_PACK",
    "HCL_PACK",
    "SQL_PACK",
    "GRAPHQL_PACK",
    "MARKDOWN_PACK",
    "MAKEFILE_PACK",
    "DOCKERFILE_PACK",
    "REGEX_PACK",
    "REQUIREMENTS_PACK",
]

# DATA/CONFIG/BUILD formats (symbol queries only, no import/type/scope)

_MARKDOWN_SYMBOLS = SymbolQueryConfig(
    query_text="(section (atx_heading (inline) @name)) @node",
    patterns=(SymbolPattern(kind="heading"),),
)

_TOML_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (table
            [(bare_key) (dotted_key)] @name) @node
        (pair
            (bare_key) @name) @node
    """,
    patterns=(SymbolPattern(kind="table"), SymbolPattern(kind="pair")),
)

_YAML_SYMBOLS = SymbolQueryConfig(
    query_text="(block_mapping_pair key: (flow_node) @name) @node",
    patterns=(SymbolPattern(kind="key"),),
)

_JSON_SYMBOLS = SymbolQueryConfig(
    query_text="(pair key: (string (string_content) @name)) @node",
    patterns=(SymbolPattern(kind="pair"),),
)

_HCL_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (block (identifier) @name) @node
        (attribute (identifier) @name) @node
    """,
    patterns=(SymbolPattern(kind="block"), SymbolPattern(kind="attribute")),
)

_MAKEFILE_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (rule (targets) @name) @node
        (variable_assignment (word) @name) @node
    """,
    patterns=(SymbolPattern(kind="target"), SymbolPattern(kind="variable")),
)

_DOCKERFILE_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (from_instruction (image_spec) @name) @node
        (run_instruction (shell_command) @name) @node
        (env_instruction (env_pair) @name) @node
        (copy_instruction) @node
        (workdir_instruction (path) @name) @node
        (expose_instruction (expose_port) @name) @node
        (cmd_instruction) @node
        (entrypoint_instruction) @node
    """,
    patterns=(
        SymbolPattern(kind="from"),
        SymbolPattern(kind="run"),
        SymbolPattern(kind="env"),
        SymbolPattern(kind="copy"),
        SymbolPattern(kind="workdir"),
        SymbolPattern(kind="expose"),
        SymbolPattern(kind="cmd"),
        SymbolPattern(kind="entrypoint"),
    ),
)

# MINIMAL PACKS (grammar metadata + extensions only)

ADA_PACK = LanguagePack(
    name="ada",
    grammar_name="ada",
    grammar_package="tree-sitter-ada",
    grammar_module="tree_sitter_ada",
    min_version="0.1.0",
    extensions=frozenset({"adb", "ads"}),
    symbol_config=SymbolQueryConfig(
        query_text="""
        (subprogram_body
            (procedure_specification
                (identifier) @name)) @node
    """,
        patterns=(SymbolPattern(kind="procedure"),),
    ),
    scope_types={"subprogram_body": "procedure"},
)
FORTRAN_PACK = LanguagePack(
    name="fortran",
    grammar_name="fortran",
    grammar_package="tree-sitter-fortran",
    grammar_module="tree_sitter_fortran",
    min_version="0.5.0",
    extensions=frozenset({"f90", "f95", "f03", "f08"}),
)
ODIN_PACK = LanguagePack(
    name="odin",
    grammar_name="odin",
    grammar_package="tree-sitter-odin",
    grammar_module="tree_sitter_odin",
    min_version="1.2.0",
    extensions=frozenset({"odin"}),
    symbol_config=SymbolQueryConfig(
        query_text="""
        (procedure_declaration
            (identifier) @name) @node
        (struct_declaration
            (identifier) @name) @node
    """,
        patterns=(
            SymbolPattern(kind="function"),
            SymbolPattern(kind="struct"),
        ),
    ),
    scope_types={"procedure": "function"},
)
HTML_PACK = LanguagePack(
    name="html",
    grammar_name="html",
    grammar_package="tree-sitter-html",
    grammar_module="tree_sitter_html",
    min_version="0.23.0",
    extensions=frozenset({"html", "htm"}),
)
CSS_PACK = LanguagePack(
    name="css",
    grammar_name="css",
    grammar_package="tree-sitter-css",
    grammar_module="tree_sitter_css",
    min_version="0.23.0",
    extensions=frozenset({"css"}),
    symbol_config=SymbolQueryConfig(
        query_text="""
        (rule_set
            (selectors) @name) @node
    """,
        patterns=(SymbolPattern(kind="rule"),),
    ),
    scope_types={"rule_set": "rule", "media_statement": "media"},
)
XML_PACK = LanguagePack(
    name="xml",
    grammar_name="xml",
    grammar_package="tree-sitter-xml",
    grammar_module="tree_sitter_xml",
    min_version="0.6.0",
    language_func="language_xml",
    extensions=frozenset({"xml", "xsl", "svg"}),
)
VERILOG_PACK = LanguagePack(
    name="verilog",
    grammar_name="verilog",
    grammar_package="tree-sitter-verilog",
    grammar_module="tree_sitter_verilog",
    min_version="1.0.0",
    extensions=frozenset({"v", "sv", "vhd", "vhdl"}),
    symbol_config=SymbolQueryConfig(
        query_text="""
        (module_declaration
            (module_header
                (module_keyword)
                (simple_identifier) @name)) @node
    """,
        patterns=(SymbolPattern(kind="module"),),
    ),
    scope_types={"module_declaration": "module"},
)
JSON_PACK = LanguagePack(
    name="json",
    grammar_name="json",
    grammar_package="tree-sitter-json",
    grammar_module="tree_sitter_json",
    min_version="0.24.0",
    extensions=frozenset({"json"}),
    symbol_config=_JSON_SYMBOLS,
)
YAML_PACK = LanguagePack(
    name="yaml",
    grammar_name="yaml",
    grammar_package="tree-sitter-yaml",
    grammar_module="tree_sitter_yaml",
    min_version="0.6.0",
    extensions=frozenset({"yaml", "yml"}),
    symbol_config=_YAML_SYMBOLS,
)
TOML_PACK = LanguagePack(
    name="toml",
    grammar_name="toml",
    grammar_package="tree-sitter-toml",
    grammar_module="tree_sitter_toml",
    min_version="0.6.0",
    extensions=frozenset({"toml"}),
    symbol_config=_TOML_SYMBOLS,
)
HCL_PACK = LanguagePack(
    name="hcl",
    grammar_name="hcl",
    grammar_package="tree-sitter-hcl",
    grammar_module="tree_sitter_hcl",
    min_version="1.0.0",
    extensions=frozenset({"hcl", "tf", "tfvars"}),
    symbol_config=_HCL_SYMBOLS,
    scope_types={"block": "block", "body": "body"},
)
SQL_PACK = LanguagePack(
    name="sql",
    grammar_name="sql",
    grammar_package="tree-sitter-sql",
    grammar_module="tree_sitter_sql",
    min_version="0.3.0",
    extensions=frozenset({"sql"}),
)
GRAPHQL_PACK = LanguagePack(
    name="graphql",
    grammar_name="graphql",
    grammar_package="tree-sitter-graphql",
    grammar_module="tree_sitter_graphql",
    min_version="0.1.0",
    extensions=frozenset({"graphql", "gql"}),
)
MARKDOWN_PACK = LanguagePack(
    name="markdown",
    grammar_name="markdown",
    grammar_package="tree-sitter-markdown",
    grammar_module="tree_sitter_markdown",
    min_version="0.3.0",
    extensions=frozenset({"md", "mdx", "markdown"}),
    symbol_config=_MARKDOWN_SYMBOLS,
)
MAKEFILE_PACK = LanguagePack(
    name="makefile",
    grammar_name="make",
    grammar_package="tree-sitter-make",
    grammar_module="tree_sitter_make",
    min_version="1.1.0",
    extensions=frozenset({"mk"}),
    filenames=frozenset({"makefile", "gnumakefile", "justfile"}),
    symbol_config=_MAKEFILE_SYMBOLS,
)
DOCKERFILE_PACK = LanguagePack(
    name="dockerfile",
    grammar_name="dockerfile",
    grammar_package="tree-sitter-dockerfile",
    grammar_module="tree_sitter_dockerfile",
    min_version="0.2.0",
    filenames=frozenset({"dockerfile"}),
    symbol_config=_DOCKERFILE_SYMBOLS,
    scope_types={"stage": "stage"},
)
REGEX_PACK = LanguagePack(
    name="regex",
    grammar_name="regex",
    grammar_package="tree-sitter-regex",
    grammar_module="tree_sitter_regex",
    min_version="0.24.0",
    extensions=frozenset({"regex"}),
)
REQUIREMENTS_PACK = LanguagePack(
    name="requirements",
    grammar_name="requirements",
    grammar_package="tree-sitter-requirements",
    grammar_module="tree_sitter_requirements",
    min_version="0.5.0",
    extensions=frozenset({"txt"}),
)
