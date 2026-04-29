"""Tree-sitter language-specific module declaration and namespace extraction."""

from __future__ import annotations

from typing import Any

import structlog
import tree_sitter
from tree_sitter import Query as _TSQuery
from tree_sitter import QueryCursor as _TSQueryCursor

from coderecon.index.parsing.packs import LanguagePack
from coderecon.index.parsing.treesitter_imports import _qualified_name_text
from coderecon.index.parsing.treesitter_models import _CSHARP_PREPROC_WRAPPERS

log = structlog.get_logger(__name__)


def _extract_declared_module_via_query(
    tree: tree_sitter.Tree,
    root: tree_sitter.Node,
    pack: LanguagePack,
) -> str | None:
    """Extract declared module using a tree-sitter query."""
    assert pack.declared_module_query is not None
    try:
        query = _TSQuery(tree.language, pack.declared_module_query)
    except (ValueError, RuntimeError):
        log.debug("ts_query_compile_failed", exc_info=True)
        return None
    cursor = _TSQueryCursor(query)
    matches: list[tuple[int, dict[str, list[Any]]]] = cursor.matches(root)
    if not matches:
        return None
    # For Elixir, filter to only defmodule calls
    # (Python bindings don't auto-filter #eq? predicates)
    if pack.name == "elixir":
        for _, captures in matches:
            target_nodes = captures.get("_target", [])
            if (
                target_nodes
                and target_nodes[0].text
                and target_nodes[0].text.decode("utf-8") == "defmodule"
            ):
                module_nodes = captures.get("module_node", [])
                if module_nodes and module_nodes[0].text:
                    return str(module_nodes[0].text.decode("utf-8"))
        return None
    module_node = matches[0][1].get("module_node", [None])[0]
    if module_node is None:
        return None
    # Per-language text extraction from the found node
    lang = pack.name
    if lang == "java":
        return _declared_module_java_node(module_node)
    elif lang == "kotlin":
        return _declared_module_kotlin_node(module_node)
    elif lang == "scala":
        return _declared_module_scala_node(module_node)
    elif lang == "go":
        return _declared_module_go_node(module_node)
    elif lang == "julia":
        # module_definition has identifier child
        for child in module_node.children:
            if child.type == "identifier" and child.text:
                return str(child.text.decode("utf-8"))
        return None
    elif lang == "php":
        # namespace_definition has namespace_name child
        for child in module_node.children:
            if child.type == "namespace_name":
                parts = [
                    c.text.decode("utf-8")
                    for c in child.children
                    if c.type == "name" and c.text
                ]
                return ".".join(parts) if parts else None
        return None
    elif lang == "haskell":
        # module node contains module_id children
        parts = [
            c.text.decode("utf-8")
            for c in module_node.children
            if c.type == "module_id" and c.text
        ]
        return ".".join(parts) if parts else None
    # Generic: try using node text directly
    return module_node.text.decode("utf-8") if module_node.text else None
def _extract_java_scoped_path(node: tree_sitter.Node) -> list[str]:
    """Extract path parts from a Java scoped_identifier."""
    if node.type == "identifier":
        return [node.text.decode("utf-8") if node.text else ""]
    parts: list[str] = []
    for child in node.children:
        if child.type in ("scoped_identifier", "identifier"):
            parts.extend(_extract_java_scoped_path(child))
    return parts
def _declared_module_java_node(node: tree_sitter.Node) -> str | None:
    """Extract module from a package_declaration node."""
    for child in node.children:
        if child.type == "scoped_identifier":
            parts = _extract_java_scoped_path(child)
            return ".".join(parts) if parts else None
        elif child.type == "identifier":
            return child.text.decode("utf-8") if child.text else None
    return None
def _declared_module_kotlin_node(node: tree_sitter.Node) -> str | None:
    """Extract module from a package_header node."""
    for child in node.children:
        if child.type == "qualified_identifier":
            parts = [
                c.text.decode("utf-8")
                for c in child.children
                if c.type == "identifier" and c.text
            ]
            return ".".join(parts) if parts else None
    return None
def _declared_module_scala_node(node: tree_sitter.Node) -> str | None:
    """Extract module from a package_clause node."""
    for child in node.children:
        if child.type == "package_identifier":
            parts = [
                c.text.decode("utf-8")
                for c in child.children
                if c.type == "identifier" and c.text
            ]
            return ".".join(parts) if parts else None
    return None
def _declared_module_csharp(root: tree_sitter.Node) -> str | None:
    """Extract namespace from C# file, handling nesting.
    Supports:
    - ``namespace Foo.Bar { ... }``  (block-scoped)
    - ``namespace Foo.Bar;``  (file-scoped, C# 10+)
    - ``namespace A { namespace B { ... } }``  (nested, concatenated)
    Uses ``node.text`` instead of filtering children because
    tree-sitter-c-sharp's ``qualified_name`` is recursively nested
    for 3+ segments (only the last segment is a direct ``identifier``
    child; earlier segments are wrapped in a sub-``qualified_name``).
    """
    parts: list[str] = []
    node: tree_sitter.Node = root
    while True:
        found = False
        for child in node.children:
            if child.type in (
                "namespace_declaration",
                "file_scoped_namespace_declaration",
            ):
                for sub in child.children:
                    if (
                        sub.type == "qualified_name"
                        and sub.text
                        or sub.type == "identifier"
                        and sub.text
                    ):
                        parts.append(sub.text.decode("utf-8"))
                        found = True
                        break
                if found:
                    # Look for nested namespace inside declaration_list
                    for sub in child.children:
                        if sub.type == "declaration_list":
                            node = sub
                            break
                    else:
                        # file-scoped namespace has no declaration_list;
                        # signal outer while-loop to stop as well.
                        found = False
                        break
                break
        if not found:
            break
    return ".".join(parts) if parts else None
def _declared_module_go_node(node: tree_sitter.Node) -> str | None:
    """Extract module from a package_clause node."""
    for child in node.children:
        if child.type == "package_identifier":
            return child.text.decode("utf-8") if child.text else None
    return None
def _declared_module_ruby(root: tree_sitter.Node) -> str | None:
    """Extract nested `module A; module B; end; end` → 'A::B'.
    Walks the module nesting chain and builds the full constant path.
    """
    parts: list[str] = []
    def _walk_modules(node: tree_sitter.Node) -> None:
        if node.type == "module":
            for sub in node.children:
                if sub.type == "constant" and sub.text:
                    parts.append(sub.text.decode("utf-8"))
                elif sub.type == "scope_resolution" and sub.text:
                    # e.g. `module A::B` in a single declaration
                    parts.append(sub.text.decode("utf-8"))
                elif sub.type == "body_statement":
                    # Check for nested modules
                    for body_child in sub.children:
                        if body_child.type == "module":
                            _walk_modules(body_child)
                            return  # Only follow the first nesting chain
    _walk_modules(root.children[0] if root.children else root)
    return "::.".join(parts).replace("::", ".") if parts else None
def _declared_module_ocaml(file_path: str) -> str | None:
    """Derive OCaml module name from filename.
    OCaml uses filename-based modules: each `.ml`/`.mli` file implicitly
    defines a module with the stem name, first character capitalized.
    Examples:
        ``src/array.ml`` → ``Array``
        ``src/array_intf.mli`` → ``Array_intf``
    """
    from pathlib import PurePosixPath
    stem = PurePosixPath(file_path).stem
    if not stem:
        return None
    # OCaml modules are the stem with first character capitalized
    return stem[0].upper() + stem[1:]


_CSHARP_TYPE_DECLS = frozenset({
    "class_declaration",
    "interface_declaration",
    "struct_declaration",
    "enum_declaration",
    "record_declaration",
    "record_struct_declaration",
})


def _csharp_type_names_from(
    declaration_list: tree_sitter.Node,
    ns_name: str,
    ns_map: dict[str, list[str]],
) -> None:
    """Collect type names from a declaration_list node, recursing into nested namespaces."""
    for child in declaration_list.children:
        if child.type in _CSHARP_TYPE_DECLS:
            for sub in child.children:
                if sub.type == "identifier":
                    ns_map.setdefault(ns_name, []).append(sub.text.decode("utf-8"))
                    break
        elif child.type == "namespace_declaration":
            _csharp_process_namespace(child, ns_name, ns_map)
        elif child.type in _CSHARP_PREPROC_WRAPPERS:
            _csharp_type_names_from(child, ns_name, ns_map)


def _csharp_process_namespace(
    node: tree_sitter.Node,
    parent_ns: str | None,
    ns_map: dict[str, list[str]],
) -> None:
    """Process a namespace_declaration node, composing the full namespace path."""
    ns_name = None
    for child in node.children:
        if child.type in ("qualified_name", "identifier"):
            local_ns = _qualified_name_text(child)
            ns_name = f"{parent_ns}.{local_ns}" if parent_ns else local_ns
        elif child.type == "declaration_list" and ns_name:
            _csharp_type_names_from(child, ns_name, ns_map)


def _csharp_collect_file_scoped_types(
    parent: tree_sitter.Node,
    ns_name: str,
    ns_map: dict[str, list[str]],
) -> None:
    """Collect type declarations for file-scoped namespaces, including inside preproc blocks."""
    for sibling in parent.children:
        if sibling.type in _CSHARP_TYPE_DECLS:
            for sub in sibling.children:
                if sub.type == "identifier":
                    ns_map.setdefault(ns_name, []).append(sub.text.decode("utf-8"))
                    break
        elif sibling.type in _CSHARP_PREPROC_WRAPPERS:
            _csharp_collect_file_scoped_types(sibling, ns_name, ns_map)


def _csharp_walk_for_namespaces(
    parent: tree_sitter.Node,
    root: tree_sitter.Node,
    ns_map: dict[str, list[str]],
    parent_ns: str | None = None,
) -> None:
    """Walk tree nodes, descending into preprocessor wrappers."""
    for node in parent.children:
        if node.type == "namespace_declaration":
            _csharp_process_namespace(node, parent_ns, ns_map)
        elif node.type == "file_scoped_namespace_declaration":
            ns_name = None
            for child in node.children:
                if child.type in ("qualified_name", "identifier"):
                    ns_name = _qualified_name_text(child)
                    break
            if ns_name:
                _csharp_collect_file_scoped_types(root, ns_name, ns_map)
        elif node.type in _CSHARP_PREPROC_WRAPPERS:
            _csharp_walk_for_namespaces(node, root, ns_map, parent_ns)


def extract_csharp_namespace_types(root: tree_sitter.Node) -> dict[str, list[str]]:
    """Extract namespace -> type names mapping from a C# AST.
    Handles both block-scoped and file-scoped namespace declarations,
    including nested namespace declarations with composed prefixes
    (e.g., ``namespace Outer { namespace Inner { class Foo {} } }``
    extracts ``{"Outer.Inner": ["Foo"]}``).
    Returns a dict mapping fully-qualified namespace names to lists of
    top-level type names (classes, interfaces, structs, enums) declared
    within that namespace.
    """
    ns_map: dict[str, list[str]] = {}
    _csharp_walk_for_namespaces(root, root, ns_map)
    return ns_map
