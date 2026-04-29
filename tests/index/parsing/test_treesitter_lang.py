"""Tests for index/_internal/parsing/treesitter_lang.py."""

from __future__ import annotations

from unittest.mock import MagicMock


from coderecon.index.parsing.treesitter_lang import (
    _csharp_process_namespace,
    _csharp_type_names_from,
    _declared_module_csharp,
    _declared_module_go_node,
    _declared_module_java_node,
    _declared_module_kotlin_node,
    _declared_module_ocaml,
    _declared_module_ruby,
    _declared_module_scala_node,
    _extract_java_scoped_path,
    extract_csharp_namespace_types,
)


def _mock_node(
    node_type: str,
    text: str | None = None,
    children: list[MagicMock] | None = None,
) -> MagicMock:
    """Create a mock tree-sitter node."""
    node = MagicMock()
    node.type = node_type
    node.text = text.encode("utf-8") if text is not None else None
    node.children = children or []
    return node


class TestExtractJavaScopedPath:
    """Tests for _extract_java_scoped_path — recursive Java identifier extraction."""

    def test_single_identifier(self) -> None:
        node = _mock_node("identifier", text="Foo")
        assert _extract_java_scoped_path(node) == ["Foo"]

    def test_scoped_identifier(self) -> None:
        inner = _mock_node("identifier", text="bar")
        outer_id = _mock_node("identifier", text="foo")
        scoped = _mock_node("scoped_identifier", children=[outer_id, inner])
        assert _extract_java_scoped_path(scoped) == ["foo", "bar"]

    def test_nested_scoped_identifier(self) -> None:
        a = _mock_node("identifier", text="com")
        b = _mock_node("identifier", text="example")
        inner = _mock_node("scoped_identifier", children=[a, b])
        c = _mock_node("identifier", text="app")
        outer = _mock_node("scoped_identifier", children=[inner, c])
        assert _extract_java_scoped_path(outer) == ["com", "example", "app"]


class TestDeclaredModuleJavaNode:
    """Tests for _declared_module_java_node — package_declaration extraction."""

    def test_scoped_package(self) -> None:
        a = _mock_node("identifier", text="com")
        b = _mock_node("identifier", text="example")
        scoped = _mock_node("scoped_identifier", children=[a, b])
        pkg = _mock_node("package_declaration", children=[scoped])
        assert _declared_module_java_node(pkg) == "com.example"

    def test_simple_package(self) -> None:
        ident = _mock_node("identifier", text="mypackage")
        pkg = _mock_node("package_declaration", children=[ident])
        assert _declared_module_java_node(pkg) == "mypackage"

    def test_no_matching_children(self) -> None:
        pkg = _mock_node("package_declaration", children=[
            _mock_node("semicolon", text=";"),
        ])
        assert _declared_module_java_node(pkg) is None


class TestDeclaredModuleKotlinNode:
    """Tests for _declared_module_kotlin_node — package_header extraction."""

    def test_qualified_identifier(self) -> None:
        parts = [
            _mock_node("identifier", text="com"),
            _mock_node("identifier", text="example"),
        ]
        qi = _mock_node("qualified_identifier", children=parts)
        header = _mock_node("package_header", children=[qi])
        assert _declared_module_kotlin_node(header) == "com.example"

    def test_no_qualified_identifier(self) -> None:
        header = _mock_node("package_header", children=[])
        assert _declared_module_kotlin_node(header) is None


class TestDeclaredModuleScalaNode:
    """Tests for _declared_module_scala_node — package_clause extraction."""

    def test_package_identifier(self) -> None:
        parts = [
            _mock_node("identifier", text="com"),
            _mock_node("identifier", text="example"),
        ]
        pi = _mock_node("package_identifier", children=parts)
        pkg = _mock_node("package_clause", children=[pi])
        assert _declared_module_scala_node(pkg) == "com.example"

    def test_no_package_identifier(self) -> None:
        pkg = _mock_node("package_clause", children=[])
        assert _declared_module_scala_node(pkg) is None


class TestDeclaredModuleGoNode:
    """Tests for _declared_module_go_node — package_clause extraction."""

    def test_package_identifier(self) -> None:
        pi = _mock_node("package_identifier", text="main")
        pkg = _mock_node("package_clause", children=[pi])
        assert _declared_module_go_node(pkg) == "main"

    def test_no_package_identifier(self) -> None:
        pkg = _mock_node("package_clause", children=[])
        assert _declared_module_go_node(pkg) is None


class TestDeclaredModuleCsharp:
    """Tests for _declared_module_csharp — C# namespace extraction."""

    def test_block_scoped_namespace(self) -> None:
        qname = _mock_node("qualified_name", text="Foo.Bar")
        decl_list = _mock_node("declaration_list", children=[])
        ns = _mock_node("namespace_declaration", children=[qname, decl_list])
        root = _mock_node("compilation_unit", children=[ns])
        assert _declared_module_csharp(root) == "Foo.Bar"

    def test_file_scoped_namespace(self) -> None:
        qname = _mock_node("qualified_name", text="MyApp.Core")
        ns = _mock_node("file_scoped_namespace_declaration", children=[qname])
        root = _mock_node("compilation_unit", children=[ns])
        assert _declared_module_csharp(root) == "MyApp.Core"

    def test_nested_namespaces(self) -> None:
        inner_qname = _mock_node("qualified_name", text="Inner")
        inner_list = _mock_node("declaration_list", children=[])
        inner_ns = _mock_node("namespace_declaration", children=[inner_qname, inner_list])
        outer_list = _mock_node("declaration_list", children=[inner_ns])
        outer_qname = _mock_node("qualified_name", text="Outer")
        outer_ns = _mock_node("namespace_declaration", children=[outer_qname, outer_list])
        root = _mock_node("compilation_unit", children=[outer_ns])
        assert _declared_module_csharp(root) == "Outer.Inner"

    def test_no_namespace(self) -> None:
        root = _mock_node("compilation_unit", children=[
            _mock_node("class_declaration"),
        ])
        assert _declared_module_csharp(root) is None


class TestDeclaredModuleRuby:
    """Tests for _declared_module_ruby — Ruby nested module extraction."""

    def test_single_module(self) -> None:
        const = _mock_node("constant", text="MyModule")
        body = _mock_node("body_statement", children=[])
        mod = _mock_node("module", children=[const, body])
        root = _mock_node("program", children=[mod])
        assert _declared_module_ruby(root) == "MyModule"

    def test_nested_modules(self) -> None:
        inner_const = _mock_node("constant", text="Inner")
        inner_body = _mock_node("body_statement", children=[])
        inner_mod = _mock_node("module", children=[inner_const, inner_body])
        outer_body = _mock_node("body_statement", children=[inner_mod])
        outer_const = _mock_node("constant", text="Outer")
        outer_mod = _mock_node("module", children=[outer_const, outer_body])
        root = _mock_node("program", children=[outer_mod])
        # Source uses "::." as join separator then replaces "::" → ".",
        # yielding a double dot for nested modules.
        assert _declared_module_ruby(root) == "Outer..Inner"

    def test_scope_resolution_module(self) -> None:
        scope = _mock_node("scope_resolution", text="A::B")
        body = _mock_node("body_statement", children=[])
        mod = _mock_node("module", children=[scope, body])
        root = _mock_node("program", children=[mod])
        assert _declared_module_ruby(root) == "A.B"

    def test_empty_root(self) -> None:
        root = _mock_node("program", children=[])
        assert _declared_module_ruby(root) is None


class TestDeclaredModuleOcaml:
    """Tests for _declared_module_ocaml — filename-based module derivation."""

    def test_simple_stem(self) -> None:
        assert _declared_module_ocaml("src/array.ml") == "Array"

    def test_interface_file(self) -> None:
        assert _declared_module_ocaml("lib/array_intf.mli") == "Array_intf"

    def test_single_char_stem(self) -> None:
        assert _declared_module_ocaml("a.ml") == "A"

    def test_dot_prefix_file_treated_as_hidden(self) -> None:
        # PurePosixPath(".ml").stem == ".ml" (hidden file, no extension)
        # so the function capitalizes it → ".ml" (dot preserved)
        assert _declared_module_ocaml(".ml") == ".ml"


class TestCsharpTypeNamesFrom:
    """Tests for _csharp_type_names_from and related helpers."""

    def test_collects_class_declaration(self) -> None:
        ident = _mock_node("identifier", text="MyClass")
        cls = _mock_node("class_declaration", children=[ident])
        decl_list = _mock_node("declaration_list", children=[cls])
        ns_map: dict[str, list[str]] = {}
        _csharp_type_names_from(decl_list, "Ns", ns_map)
        assert ns_map == {"Ns": ["MyClass"]}

    def test_collects_multiple_types(self) -> None:
        cls = _mock_node("class_declaration", children=[
            _mock_node("identifier", text="Foo"),
        ])
        iface = _mock_node("interface_declaration", children=[
            _mock_node("identifier", text="IBar"),
        ])
        decl_list = _mock_node("declaration_list", children=[cls, iface])
        ns_map: dict[str, list[str]] = {}
        _csharp_type_names_from(decl_list, "App", ns_map)
        assert ns_map == {"App": ["Foo", "IBar"]}


class TestCsharpProcessNamespace:
    """Tests for _csharp_process_namespace."""

    def test_composes_parent_namespace(self) -> None:
        ident = _mock_node("identifier", text="Inner")
        cls_ident = _mock_node("identifier", text="Widget")
        cls = _mock_node("class_declaration", children=[cls_ident])
        decl_list = _mock_node("declaration_list", children=[cls])
        ns_node = _mock_node("namespace_declaration", children=[ident, decl_list])
        ns_map: dict[str, list[str]] = {}
        _csharp_process_namespace(ns_node, "Outer", ns_map)
        assert ns_map == {"Outer.Inner": ["Widget"]}

    def test_no_parent_namespace(self) -> None:
        ident = _mock_node("identifier", text="Root")
        decl_list = _mock_node("declaration_list", children=[])
        ns_node = _mock_node("namespace_declaration", children=[ident, decl_list])
        ns_map: dict[str, list[str]] = {}
        _csharp_process_namespace(ns_node, None, ns_map)
        assert ns_map == {}  # empty decl_list


class TestExtractCsharpNamespaceTypes:
    """Tests for extract_csharp_namespace_types — full namespace->types mapping."""

    def test_block_scoped_namespace_with_class(self) -> None:
        ident = _mock_node("identifier", text="MyNs")
        cls_ident = _mock_node("identifier", text="MyClass")
        cls = _mock_node("class_declaration", children=[cls_ident])
        decl_list = _mock_node("declaration_list", children=[cls])
        ns = _mock_node("namespace_declaration", children=[ident, decl_list])
        root = _mock_node("compilation_unit", children=[ns])
        result = extract_csharp_namespace_types(root)
        assert result == {"MyNs": ["MyClass"]}

    def test_file_scoped_namespace_with_class(self) -> None:
        ident = _mock_node("identifier", text="FileNs")
        ns = _mock_node("file_scoped_namespace_declaration", children=[ident])
        cls_ident = _mock_node("identifier", text="FileClass")
        cls = _mock_node("class_declaration", children=[cls_ident])
        root = _mock_node("compilation_unit", children=[ns, cls])
        result = extract_csharp_namespace_types(root)
        assert result == {"FileNs": ["FileClass"]}

    def test_empty_tree_returns_empty(self) -> None:
        root = _mock_node("compilation_unit", children=[])
        result = extract_csharp_namespace_types(root)
        assert result == {}
