"""Tree-sitter import and dynamic access extraction helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
import tree_sitter
from tree_sitter import Query as _TSQuery
from tree_sitter import QueryCursor as _TSQueryCursor

if TYPE_CHECKING:
    from coderecon.index._internal.parsing.packs import ImportQueryConfig

from coderecon.index._internal.parsing.packs import LanguagePack
from coderecon.index._internal.parsing.treesitter_models import (
    DynamicAccess,
    SyntacticImport,
    _import_uid,
)

log = structlog.get_logger(__name__)


def _extract_imports_declarative(
    tree: tree_sitter.Tree,
    root: tree_sitter.Node,
    config: ImportQueryConfig,
    file_path: str,
) -> list[SyntacticImport]:
    """Extract imports using declarative multi-pattern queries.
    Each query pattern captures structured fields directly via named
    captures (@source, @name, @alias, @node).  The pattern index maps
    to an import_kind string via ``config.pattern_kinds``.
    This single method replaces ALL per-language ``_process_*_import_node``
    handlers.
    """
    try:
        query = _TSQuery(tree.language, config.query)
    except (ValueError, RuntimeError):
        log.debug("ts_query_compile_failed", exc_info=True)
        return []
    cursor = _TSQueryCursor(query)
    matches: list[tuple[int, dict[str, list[Any]]]] = cursor.matches(root)
    imports: list[SyntacticImport] = []
    for pattern_idx, captures in matches:
        source_nodes = captures.get(config.source_capture, [])
        name_nodes = captures.get(config.name_capture, [])
        alias_nodes = captures.get(config.alias_capture, [])
        node_list = captures.get("node", [])
        text_alias: str | None = None  # alias parsed from node text
        # Determine source text
        source = ""
        if source_nodes:
            source = source_nodes[0].text.decode("utf-8") if source_nodes[0].text else ""
        elif config.source_from_node_text and node_list:
            # Use the full node text as import source (e.g. Scala, C#)
            raw = node_list[0].text.decode("utf-8") if node_list[0].text else ""
            # Strip common prefixes and trailing semicolons
            for prefix in ("import static ", "import ", "using static ", "using "):
                if raw.startswith(prefix):
                    raw = raw[len(prefix) :]
                    break
            source = raw.rstrip("; \n\t")
            # Handle aliased imports (e.g. "MyAlias = System.Collections.X")
            if " = " in source:
                parts = source.split(" = ", 1)
                text_alias = parts[0].strip()
                source = parts[1].strip()
        if config.strip_quotes:
            source = source.strip("'\"`")
        if config.strip_angle_brackets:
            source = source.strip("<>").strip("'\"`")
        # Determine imported name
        name = ""
        # Check for per-pattern name override first (e.g., "*.go" dot import → "*")
        if pattern_idx in config.name_overrides:
            name = config.name_overrides[pattern_idx]
        elif name_nodes:
            name = name_nodes[0].text.decode("utf-8") if name_nodes[0].text else ""
        elif source and config.name_from_source_segment:
            # Use last path segment (e.g., "path/filepath" → "filepath")
            name = source.rsplit(config.source_segment_sep, 1)[-1]
        elif source:
            name = source
        if not name and not source:
            continue
        if not name:
            name = source
        # Determine alias
        alias: str | None = None
        if alias_nodes:
            alias = alias_nodes[0].text.decode("utf-8") if alias_nodes[0].text else None
        elif text_alias:
            alias = text_alias
        # Determine line/col from @node or first available capture
        anchor = None
        if node_list:
            anchor = node_list[0]
        elif source_nodes:
            anchor = source_nodes[0]
        elif name_nodes:
            anchor = name_nodes[0]
        if anchor is None:
            continue
        # Determine import_kind
        kind = config.pattern_kinds.get(pattern_idx, "import")
        if config.kind_from_capture:
            fn_nodes = captures.get(config.kind_from_capture, [])
            if fn_nodes:
                fn_name = fn_nodes[0].text.decode("utf-8") if fn_nodes[0].text else ""
                kind = config.kind_from_capture_map.get(fn_name, kind)
        imports.append(
            SyntacticImport(
                import_uid=_import_uid(file_path, name, anchor.start_point[0] + 1),
                imported_name=name,
                alias=alias,
                source_literal=source if source else None,
                import_kind=kind,
                start_line=anchor.start_point[0] + 1,
                start_col=anchor.start_point[1],
                end_line=anchor.end_point[0] + 1,
                end_col=anchor.end_point[1],
            )
        )
    return imports

def _extract_dynamic_via_query(
    tree: tree_sitter.Tree,
    root: tree_sitter.Node,
    pack: LanguagePack,
) -> list[DynamicAccess]:
    """Extract dynamic accesses using a query to find candidate nodes."""
    assert pack.dynamic_query is not None
    try:
        query = _TSQuery(tree.language, pack.dynamic_query)
    except (ValueError, RuntimeError):
        log.debug("ts_query_compile_failed", exc_info=True)
        return []
    cursor = _TSQueryCursor(query)
    matches: list[tuple[int, dict[str, list[Any]]]] = cursor.matches(root)
    processor = {
        "python": _process_python_dynamic_node,
        "javascript": _process_js_dynamic_node,
        "typescript": _process_js_dynamic_node,
        "tsx": _process_js_dynamic_node,
    }.get(pack.name, lambda _n: [])
    dynamics: list[DynamicAccess] = []
    for _idx, captures in matches:
        nodes = captures.get("dynamic_node", [])
        for node in nodes:
            dynamics.extend(processor(node))
    return dynamics

def _process_python_import_node(node: tree_sitter.Node, file_path: str) -> list[SyntacticImport]:
    """Process a single Python import node found by query."""
    imports: list[SyntacticImport] = []
    if node.type == "import_statement":
        for child in node.children:
            if child.type == "dotted_name":
                name = child.text.decode("utf-8") if child.text else ""
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(file_path, name, node.start_point[0] + 1),
                        imported_name=name,
                        alias=None,
                        source_literal=name,
                        import_kind="python_import",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        end_line=node.end_point[0] + 1,
                        end_col=node.end_point[1],
                    )
                )
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node:
                    name = name_node.text.decode("utf-8") if name_node.text else ""
                    alias = (
                        alias_node.text.decode("utf-8")
                        if alias_node and alias_node.text
                        else None
                    )
                    imports.append(
                        SyntacticImport(
                            import_uid=_import_uid(file_path, name, node.start_point[0] + 1),
                            imported_name=name,
                            alias=alias,
                            source_literal=name,
                            import_kind="python_import",
                            start_line=node.start_point[0] + 1,
                            start_col=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_col=node.end_point[1],
                        )
                    )
    elif node.type == "import_from_statement":
        module_node = node.child_by_field_name("module_name")
        source = module_node.text.decode("utf-8") if module_node and module_node.text else None
        for child in node.children:
            if child.type == "dotted_name" and child != module_node:
                name = child.text.decode("utf-8") if child.text else ""
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(file_path, name, node.start_point[0] + 1),
                        imported_name=name,
                        alias=None,
                        source_literal=source,
                        import_kind="python_from",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        end_line=node.end_point[0] + 1,
                        end_col=node.end_point[1],
                    )
                )
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node:
                    name = name_node.text.decode("utf-8") if name_node.text else ""
                    alias = (
                        alias_node.text.decode("utf-8")
                        if alias_node and alias_node.text
                        else None
                    )
                    imports.append(
                        SyntacticImport(
                            import_uid=_import_uid(file_path, name, node.start_point[0] + 1),
                            imported_name=name,
                            alias=alias,
                            source_literal=source,
                            import_kind="python_from",
                            start_line=node.start_point[0] + 1,
                            start_col=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_col=node.end_point[1],
                        )
                    )
            elif child.type == "wildcard_import":
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(file_path, "*", node.start_point[0] + 1),
                        imported_name="*",
                        alias=None,
                        source_literal=source,
                        import_kind="python_from",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        end_line=node.end_point[0] + 1,
                        end_col=node.end_point[1],
                    )
                )
    return imports
# C# using directive and namespace extraction
@staticmethod

def _qualified_name_text(node: tree_sitter.Node) -> str:
    """Extract full text of a qualified_name or identifier node."""
    if node.text:
        text: str = node.text.decode("utf-8")
        return text
    return ""

def _process_python_dynamic_node(node: tree_sitter.Node) -> list[DynamicAccess]:
    """Process a single Python dynamic-access node found by query."""
    dynamics: list[DynamicAccess] = []
    if node.type == "call":
        func_node = node.child_by_field_name("function")
        if func_node and func_node.type == "identifier":
            func_name = func_node.text.decode("utf-8") if func_node.text else ""
            if func_name in ("getattr", "setattr", "hasattr", "delattr"):
                args_node = node.child_by_field_name("arguments")
                literals: list[str] = []
                has_dynamic = False
                if args_node:
                    for i, arg in enumerate(args_node.children):
                        if i == 1:
                            if arg.type == "string":
                                literal = (
                                    arg.text.decode("utf-8").strip("'\"") if arg.text else ""
                                )
                                literals.append(literal)
                            else:
                                has_dynamic = True
                dynamics.append(
                    DynamicAccess(
                        pattern_type="getattr",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        extracted_literals=literals,
                        has_non_literal_key=has_dynamic,
                    )
                )
            elif func_name in ("eval", "exec"):
                dynamics.append(
                    DynamicAccess(
                        pattern_type="eval",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        has_non_literal_key=True,
                    )
                )
    elif node.type == "subscript":
        subscript_node = node.child_by_field_name("subscript")
        sub_literals: list[str] = []
        sub_has_dynamic = True
        if subscript_node and subscript_node.type == "string":
            literal = (
                subscript_node.text.decode("utf-8").strip("'\"") if subscript_node.text else ""
            )
            sub_literals.append(literal)
            sub_has_dynamic = False
        dynamics.append(
            DynamicAccess(
                pattern_type="bracket_access",
                start_line=node.start_point[0] + 1,
                start_col=node.start_point[1],
                extracted_literals=sub_literals,
                has_non_literal_key=sub_has_dynamic,
            )
        )
    return dynamics

def _process_js_dynamic_node(node: tree_sitter.Node) -> list[DynamicAccess]:
    """Process a single JS/TS dynamic-access node found by query."""
    dynamics: list[DynamicAccess] = []
    if node.type == "subscript_expression":
        index_node = node.child_by_field_name("index")
        literals: list[str] = []
        has_dynamic = True
        if index_node and index_node.type == "string":
            literal = index_node.text.decode("utf-8").strip("'\"") if index_node.text else ""
            literals.append(literal)
            has_dynamic = False
        dynamics.append(
            DynamicAccess(
                pattern_type="bracket_access",
                start_line=node.start_point[0] + 1,
                start_col=node.start_point[1],
                extracted_literals=literals,
                has_non_literal_key=has_dynamic,
            )
        )
    elif node.type == "call_expression":
        func_node = node.child_by_field_name("function")
        if func_node and func_node.type == "identifier":
            func_name = func_node.text.decode("utf-8") if func_node.text else ""
            if func_name == "eval":
                dynamics.append(
                    DynamicAccess(
                        pattern_type="eval",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        has_non_literal_key=True,
                    )
                )
    return dynamics

def _process_js_import_node(node: tree_sitter.Node, file_path: str) -> list[SyntacticImport]:
    """Process a single JS/TS import node found by query."""
    imports: list[SyntacticImport] = []
    if node.type == "import_statement":
        source_node = node.child_by_field_name("source")
        source = None
        if source_node and source_node.text:
            source = source_node.text.decode("utf-8").strip("'\"")
        for child in node.children:
            if child.type != "import_clause":
                continue
            for clause_child in child.children:
                if clause_child.type == "identifier":
                    name = clause_child.text.decode("utf-8") if clause_child.text else ""
                    imports.append(
                        SyntacticImport(
                            import_uid=_import_uid(
                                file_path, name, node.start_point[0] + 1
                            ),
                            imported_name=name,
                            alias=None,
                            source_literal=source,
                            import_kind="js_import",
                            start_line=node.start_point[0] + 1,
                            start_col=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_col=node.end_point[1],
                        )
                    )
                elif clause_child.type == "named_imports":
                    for spec in clause_child.children:
                        if spec.type != "import_specifier":
                            continue
                        name_node = spec.child_by_field_name("name")
                        alias_node = spec.child_by_field_name("alias")
                        if not (name_node and name_node.text):
                            continue
                        name = name_node.text.decode("utf-8")
                        alias = (
                            alias_node.text.decode("utf-8")
                            if alias_node and alias_node.text
                            else None
                        )
                        imports.append(
                            SyntacticImport(
                                import_uid=_import_uid(
                                    file_path, name, node.start_point[0] + 1
                                ),
                                imported_name=name,
                                alias=alias,
                                source_literal=source,
                                import_kind="js_import",
                                start_line=node.start_point[0] + 1,
                                start_col=node.start_point[1],
                                end_line=node.end_point[0] + 1,
                                end_col=node.end_point[1],
                            )
                        )
                elif clause_child.type == "namespace_import":
                    for ns_child in clause_child.children:
                        if ns_child.type != "identifier":
                            continue
                        alias = ns_child.text.decode("utf-8") if ns_child.text else ""
                        imports.append(
                            SyntacticImport(
                                import_uid=_import_uid(
                                    file_path, "*", node.start_point[0] + 1
                                ),
                                imported_name="*",
                                alias=alias,
                                source_literal=source,
                                import_kind="js_import",
                                start_line=node.start_point[0] + 1,
                                start_col=node.start_point[1],
                                end_line=node.end_point[0] + 1,
                                end_col=node.end_point[1],
                            )
                        )
    elif node.type == "call_expression":
        func_node = node.child_by_field_name("function")
        if func_node and func_node.text and func_node.text.decode("utf-8") == "require":
            args_node = node.child_by_field_name("arguments")
            if args_node and args_node.children:
                for arg in args_node.children:
                    if arg.type == "string":
                        source = arg.text.decode("utf-8").strip("'\"") if arg.text else None
                        imports.append(
                            SyntacticImport(
                                import_uid=_import_uid(
                                    file_path,
                                    source or "require",
                                    node.start_point[0] + 1,
                                ),
                                imported_name=source or "require",
                                alias=None,
                                source_literal=source,
                                import_kind="js_require",
                                start_line=node.start_point[0] + 1,
                                start_col=node.start_point[1],
                                end_line=node.end_point[0] + 1,
                                end_col=node.end_point[1],
                            )
                        )
                        break
    return imports
# Unified query-based symbol extraction
