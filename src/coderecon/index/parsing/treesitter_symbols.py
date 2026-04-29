"""Tree-sitter symbol extraction helpers."""

from __future__ import annotations

import re
from typing import Any

import structlog
import tree_sitter
from tree_sitter import Query as _TSQuery
from tree_sitter import QueryCursor as _TSQueryCursor

from coderecon.index.parsing.packs import SymbolQueryConfig
from coderecon.index.parsing.treesitter_models import SyntacticSymbol

log = structlog.get_logger(__name__)


def _extract_symbols_via_query(
    tree: tree_sitter.Tree,
    root: tree_sitter.Node,
    config: SymbolQueryConfig,
) -> list[SyntacticSymbol]:
    """Extract symbols using a tree-sitter query.
    This is the unified extraction path for all query-capable languages.
    Each language defines a ``SymbolQueryConfig`` with:
    - ``query_text``  — S-expression patterns with @name, @node, @params
    - ``patterns``    — ordered mapping of pattern index → SymbolPattern
    - ``container_types`` — node types that establish parent context
    The executor:
    1. Compiles and runs the query against the parse tree.
    2. Resolves parent context (parent_name) by walking ancestors.
    3. Adjusts kind (e.g. function → method) when nested.
    4. Extracts signature from @params capture.
    """
    ts_lang = tree.language
    query = _TSQuery(ts_lang, config.query_text)
    cursor = _TSQueryCursor(query)
    matches: list[tuple[int, dict[str, list[Any]]]] = cursor.matches(root)
    symbols: list[SyntacticSymbol] = []
    for pattern_idx, captures in matches:
        if pattern_idx >= len(config.patterns):
            continue  # Defensive: extra patterns (e.g. #eq? helpers)
        pattern = config.patterns[pattern_idx]
        name_nodes = captures.get("name")
        name: str = pattern.kind if not name_nodes else str(name_nodes[0].text.decode("utf-8"))
        node_list = captures.get("node")
        node = node_list[0] if node_list else (name_nodes[0] if name_nodes else None)
        if node is None:
            continue
        kind = pattern.kind
        parent_name: str | None = None
        if config.container_types:
            parent_name = _find_container_name(
                node, config.container_types, config.container_name_field
            )
            if parent_name and pattern.nested_kind:
                kind = pattern.nested_kind
        signature = _extract_signature(captures, node, config.params_from_children)
        decorators = _extract_decorators(node)
        return_type = _extract_return_type(node)
        docstring = _extract_docstring(node, config.body_node_types)
        symbols.append(
            SyntacticSymbol(
                name=name,
                kind=kind,
                line=node.start_point[0] + 1,
                column=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_column=node.end_point[1],
                signature=signature,
                parent_name=parent_name,
                signature_text=signature,
                decorators=decorators,
                docstring=docstring,
                return_type=return_type,
            )
        )
    return symbols
@staticmethod
def _find_container_name(
    node: tree_sitter.Node,
    container_types: frozenset[str],
    name_field: str,
) -> str | None:
    """Walk ancestors to find the nearest container and return its name."""
    current = node.parent
    while current is not None:
        if current.type in container_types:
            name_node = current.child_by_field_name(name_field)
            if name_node:
                return str(name_node.text.decode("utf-8"))
            # Some containers (e.g. Ruby module) use constant children
            for child in current.children:
                if child.type in ("constant", "identifier", "type_identifier"):
                    return str(child.text.decode("utf-8"))
            return None
        current = current.parent
    return None
@staticmethod
def _extract_signature(
    captures: dict[str, list[Any]],
    node: tree_sitter.Node,
    params_from_children: bool,
) -> str | None:
    """Extract signature from query captures or node children.
    Three strategies in order of priority:
    1. Use @params capture from query (most languages).
    2. Collect 'parameter' children between '(' and ')' (Swift, OCaml).
    3. Return None if no signature can be determined.
    """
    # Strategy 1: @params capture
    params_list = captures.get("params")
    if params_list:
        return str(params_list[0].text.decode("utf-8"))
    # Strategy 2: collect parameter children (e.g. Swift)
    if params_from_children:
        params: list[str] = []
        in_params = False
        for child in node.children:
            if child.type == "(":
                in_params = True
            elif child.type == ")":
                break
            elif in_params and child.type == "parameter":
                params.append(child.text.decode("utf-8"))
        if params or in_params:
            return "(" + ", ".join(params) + ")"
    return None
def _extract_decorators(node: tree_sitter.Node) -> list[str] | None:
    """Extract decorator/annotation strings from a definition node.
    Language-agnostic strategy:
    1. Python: parent is 'decorated_definition' → collect 'decorator' children.
    2. Java/C#/Kotlin/PHP: node itself has 'modifiers' child with annotations.
    3. Rust: preceding 'attribute_item' siblings.
    4. Otherwise: return None.
    """
    decorators: list[str] = []
    # Strategy 1: Python decorated_definition parent
    parent = node.parent
    if parent is not None and parent.type == "decorated_definition":
        for child in parent.children:
            if child.type == "decorator":
                decorators.append(child.text.decode("utf-8").strip())
        if decorators:
            return decorators
    # Strategy 2: Modifiers/attribute children on node itself
    # Covers Java, C#, Kotlin, Scala, PHP
    _annotation_types = frozenset(
        {
            "annotation",
            "marker_annotation",
            "attribute_list",
            "attribute",
            "single_annotation",
            "multi_annotation",
            "user_type",  # Kotlin annotations
        }
    )
    for child in node.children:
        if child.type == "modifiers":
            for mod_child in child.children:
                if mod_child.type in _annotation_types:
                    decorators.append(mod_child.text.decode("utf-8").strip())
        elif child.type in _annotation_types:
            decorators.append(child.text.decode("utf-8").strip())
    # Strategy 3: Rust attribute_item siblings preceding the node
    if not decorators and parent is not None:
        for sibling in parent.children:
            if sibling == node:
                break
            if sibling.type == "attribute_item":
                decorators.append(sibling.text.decode("utf-8").strip())
    return decorators if decorators else None
def _extract_return_type(node: tree_sitter.Node) -> str | None:
    """Extract return type annotation from a definition node.
    Language-agnostic: checks common field names used across grammars.
    """
    # Most languages use 'return_type' or 'type' as the field name
    for field_name in ("return_type", "type"):
        type_node = node.child_by_field_name(field_name)
        if type_node is not None:
            text: str = str(type_node.text.decode("utf-8")).strip()
            # Avoid returning the whole body if 'type' matched something too big
            if len(text) < 200:
                return text
    # Check for return type indicated by '->' or ':' followed by type
    # (TypeScript/Rust arrow return types handled by field names above)
    return None
@staticmethod
def _extract_docstring(
    node: tree_sitter.Node,
    body_node_types: frozenset[str],
) -> str | None:
    """Extract docstring from a definition node.
    Three strategies:
    1. Python-style: body's first statement is expression_statement(string).
    2. Block comment: preceding sibling block/comment node (JSDoc, Javadoc).
    3. Line comments: consecutive preceding /// or // doc-comment siblings.
    """
    _comment_types = frozenset({"comment", "line_comment", "block_comment"})
    # Strategy 1: Python docstrings (first expression_statement > string in body)
    for child in node.children:
        if child.type in body_node_types:
            body = child
            if body.child_count > 0:
                first = body.children[0]
                if first.type == "expression_statement" and first.child_count > 0:
                    string_node = first.children[0]
                    if string_node.type == "string":
                        raw = string_node.text.decode("utf-8").strip()
                        # Strip triple quotes
                        for q in ('"""', "'''"):
                            if raw.startswith(q) and raw.endswith(q):
                                raw = raw[3:-3].strip()
                                break
                        # Take first paragraph only
                        first_para = raw.split("\n\n")[0].strip()
                        if first_para:
                            # Normalize whitespace
                            return " ".join(first_para.split())
            break  # Only check first body child
    # Strategy 2+3: Preceding sibling comment(s)
    prev = node.prev_named_sibling
    if prev is None or prev.type not in _comment_types:
        return None
    text = prev.text.decode("utf-8").strip()
    # Strategy 2: Block doc-comment (/** ... */)
    if text.startswith("/**"):
        text = text[3:]
        if text.endswith("*/"):
            text = text[:-2]
        text = text.strip()
        lines = []
        for line in text.splitlines():
            clean = line.strip().lstrip("* ").strip()
            lines.append(clean)
        full = " ".join(lines)
        first_para = full.split("\n\n")[0].strip()
        if first_para:
            return " ".join(first_para.split())
    # Strategy 3: Consecutive /// line-comments (Rust, C#, etc.)
    if text.startswith("///"):
        # Walk backward collecting all consecutive /// lines
        doc_lines: list[str] = []
        sibling = prev
        while sibling is not None and sibling.type in _comment_types:
            sib_text = sibling.text.decode("utf-8").strip()
            if sib_text.startswith("///"):
                doc_lines.append(sib_text[3:].strip())
                sibling = sibling.prev_named_sibling
            else:
                break
        # Lines were collected in reverse order
        doc_lines.reverse()
        # Strip XML tags (C# style) and take meaningful content
        cleaned: list[str] = []
        for line in doc_lines:
            # Remove XML tags like <summary>, </summary>, <param>, etc.
            stripped = re.sub(r"<[^>]+>", "", line).strip()
            if stripped:
                cleaned.append(stripped)
        full = " ".join(cleaned)
        first_para = full.split("\n\n")[0].strip()
        if first_para:
            return " ".join(first_para.split())
    return None
def _extract_generic_symbols(root: tree_sitter.Node, _language: str) -> list[SyntacticSymbol]:
    """Generic symbol extraction by walking the tree."""
    symbols: list[SyntacticSymbol] = []
    # Look for common definition patterns
    def_types = {
        "function_definition",
        "function_declaration",
        "method_definition",
        "method_declaration",
        "class_definition",
        "class_declaration",
        "struct_definition",
        "struct_item",
        "enum_definition",
        "enum_item",
        "enum_declaration",
        "interface_declaration",
        "type_declaration",
        "trait_item",
        # C# record types (SYNC: resolver.py _TYPE_KINDS)
        "record_declaration",
        "record_struct_declaration",
    }
    def walk(node: tree_sitter.Node) -> None:
        if node.type in def_types:
            name_node = node.child_by_field_name("name")
            if name_node:
                name = name_node.text.decode("utf-8")
                kind = node.type.replace("_definition", "").replace("_declaration", "")
                symbols.append(
                    SyntacticSymbol(
                        name=name,
                        kind=kind,
                        line=node.start_point[0] + 1,
                        column=node.start_point[1],
                        end_line=node.end_point[0] + 1,
                        end_column=node.end_point[1],
                    )
                )
        for child in node.children:
            walk(child)
    walk(root)
    return symbols
