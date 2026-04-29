"""Tests for index/_internal/parsing/treesitter_symbols.py module.

Covers:
- _extract_generic_symbols() tree walking
- _extract_return_type() field extraction
- _extract_decorators() strategy dispatch
- _extract_signature() capture vs. child strategies
- _find_container_name() ancestor walking
- _extract_docstring() multi-strategy extraction
"""

from __future__ import annotations

from unittest.mock import MagicMock

from coderecon.index.parsing.treesitter_symbols import (
    _extract_decorators,
    _extract_docstring,
    _extract_generic_symbols,
    _extract_return_type,
    _extract_signature,
    _find_container_name,
)


def _make_node(
    node_type: str,
    text: str = "",
    *,
    children: list[MagicMock] | None = None,
    parent: MagicMock | None = None,
    start_point: tuple[int, int] = (0, 0),
    end_point: tuple[int, int] = (0, 0),
    fields: dict[str, MagicMock | None] | None = None,
    prev_named_sibling: MagicMock | None = None,
) -> MagicMock:
    """Build a mock tree-sitter Node."""
    node = MagicMock()
    node.type = node_type
    node.text = text.encode("utf-8")
    node.children = children or []
    node.child_count = len(node.children)
    node.parent = parent
    node.start_point = start_point
    node.end_point = end_point
    node.prev_named_sibling = prev_named_sibling

    _fields = fields or {}

    def _child_by_field_name(name: str) -> MagicMock | None:
        return _fields.get(name)

    node.child_by_field_name = _child_by_field_name
    return node


class TestExtractGenericSymbols:
    """_extract_generic_symbols walks node tree for common definition types."""

    def test_empty_tree(self) -> None:
        root = _make_node("module")
        result = _extract_generic_symbols(root, "python")
        assert result == []

    def test_function_definition(self) -> None:
        name_node = _make_node("identifier", "my_func")
        func = _make_node(
            "function_definition",
            children=[name_node],
            start_point=(5, 0),
            end_point=(10, 0),
            fields={"name": name_node},
        )
        root = _make_node("module", children=[func])
        result = _extract_generic_symbols(root, "python")
        assert len(result) == 1
        assert result[0].name == "my_func"
        assert result[0].kind == "function"
        # line is 1-indexed: start_point[0] + 1
        assert result[0].line == 6

    def test_class_declaration(self) -> None:
        name_node = _make_node("identifier", "MyClass")
        cls = _make_node(
            "class_declaration",
            children=[name_node],
            start_point=(0, 0),
            end_point=(20, 0),
            fields={"name": name_node},
        )
        root = _make_node("module", children=[cls])
        result = _extract_generic_symbols(root, "csharp")
        assert len(result) == 1
        assert result[0].name == "MyClass"
        assert result[0].kind == "class"

    def test_nested_definitions(self) -> None:
        inner_name = _make_node("identifier", "inner")
        inner_func = _make_node(
            "method_declaration",
            children=[inner_name],
            start_point=(3, 4),
            end_point=(5, 4),
            fields={"name": inner_name},
        )
        outer_name = _make_node("identifier", "Outer")
        outer_cls = _make_node(
            "class_definition",
            children=[outer_name, inner_func],
            start_point=(1, 0),
            end_point=(6, 0),
            fields={"name": outer_name},
        )
        root = _make_node("module", children=[outer_cls])
        result = _extract_generic_symbols(root, "python")
        assert len(result) == 2
        names = {s.name for s in result}
        assert names == {"Outer", "inner"}

    def test_no_name_field_skipped(self) -> None:
        func = _make_node(
            "function_definition",
            children=[],
            start_point=(0, 0),
            end_point=(1, 0),
            fields={},
        )
        root = _make_node("module", children=[func])
        result = _extract_generic_symbols(root, "python")
        assert result == []

    def test_record_declaration_recognized(self) -> None:
        """record_declaration is in the def_types set."""
        name_node = _make_node("identifier", "MyRecord")
        rec = _make_node(
            "record_declaration",
            children=[name_node],
            start_point=(0, 0),
            end_point=(2, 0),
            fields={"name": name_node},
        )
        root = _make_node("module", children=[rec])
        result = _extract_generic_symbols(root, "csharp")
        assert len(result) == 1
        assert result[0].kind == "record"


class TestFindContainerName:
    """_find_container_name walks ancestors for container types."""

    def test_no_container_returns_none(self) -> None:
        node = _make_node("identifier")
        node.parent = None
        result = _find_container_name(node, frozenset({"class_definition"}), "name")
        assert result is None

    def test_finds_parent_class(self) -> None:
        class_name_node = _make_node("identifier", "MyClass")
        class_node = _make_node(
            "class_definition",
            fields={"name": class_name_node},
        )
        class_node.parent = None
        method_node = _make_node("function_definition")
        method_node.parent = class_node
        result = _find_container_name(
            method_node, frozenset({"class_definition"}), "name"
        )
        assert result == "MyClass"

    def test_skips_non_container_ancestors(self) -> None:
        class_name = _make_node("identifier", "Top")
        class_node = _make_node("class_definition", fields={"name": class_name})
        class_node.parent = None
        block_node = _make_node("block")
        block_node.parent = class_node
        inner_node = _make_node("function_definition")
        inner_node.parent = block_node
        result = _find_container_name(
            inner_node, frozenset({"class_definition"}), "name"
        )
        assert result == "Top"

    def test_fallback_to_constant_child(self) -> None:
        """If no 'name' field, falls back to constant/identifier children."""
        const_child = _make_node("constant", "ModuleName")
        container = _make_node("module_definition", fields={}, children=[const_child])
        container.parent = None
        node = _make_node("function_definition")
        node.parent = container
        result = _find_container_name(
            node, frozenset({"module_definition"}), "name"
        )
        assert result == "ModuleName"


class TestExtractSignature:
    """_extract_signature uses @params capture or child collection."""

    def test_params_capture_preferred(self) -> None:
        params_node = _make_node("parameters", "(x, y)")
        captures = {"params": [params_node]}
        node = _make_node("function_definition")
        result = _extract_signature(captures, node, params_from_children=False)
        assert result == "(x, y)"

    def test_params_from_children(self) -> None:
        open_paren = _make_node("(")
        param1 = _make_node("parameter", "a: Int")
        param2 = _make_node("parameter", "b: String")
        close_paren = _make_node(")")
        node = _make_node(
            "function_definition",
            children=[open_paren, param1, param2, close_paren],
        )
        result = _extract_signature({}, node, params_from_children=True)
        assert result == "(a: Int, b: String)"

    def test_no_params_returns_none(self) -> None:
        node = _make_node("function_definition")
        result = _extract_signature({}, node, params_from_children=False)
        assert result is None

    def test_empty_params_from_children(self) -> None:
        """Parens with no parameter children → '()'."""
        open_paren = _make_node("(")
        close_paren = _make_node(")")
        node = _make_node("function_definition", children=[open_paren, close_paren])
        result = _extract_signature({}, node, params_from_children=True)
        assert result == "()"


class TestExtractDecorators:
    """_extract_decorators retrieves decorators from definition nodes."""

    def test_python_decorated_definition(self) -> None:
        dec_node = _make_node("decorator", "@property")
        parent = _make_node("decorated_definition", children=[dec_node])
        node = _make_node("function_definition")
        node.parent = parent
        result = _extract_decorators(node)
        assert result == ["@property"]

    def test_no_decorators_returns_none(self) -> None:
        node = _make_node("function_definition")
        node.parent = _make_node("module")
        result = _extract_decorators(node)
        assert result is None

    def test_annotation_child_on_node(self) -> None:
        """Java/C# style: annotation child directly on the node."""
        ann = _make_node("annotation", "@Override")
        node = _make_node("method_declaration", children=[ann])
        node.parent = _make_node("class_body")
        result = _extract_decorators(node)
        assert result == ["@Override"]

    def test_modifiers_with_annotations(self) -> None:
        """Java-style: modifiers child containing annotation children."""
        ann = _make_node("marker_annotation", "@Deprecated")
        mods = _make_node("modifiers", children=[ann])
        node = _make_node("method_declaration", children=[mods])
        node.parent = _make_node("class_body")
        result = _extract_decorators(node)
        assert result == ["@Deprecated"]

    def test_rust_attribute_siblings(self) -> None:
        """Rust: attribute_item siblings preceding the node."""
        attr = _make_node("attribute_item", "#[derive(Debug)]")
        node = _make_node("struct_item")
        parent = _make_node("source_file", children=[attr, node])
        node.parent = parent
        result = _extract_decorators(node)
        assert result == ["#[derive(Debug)]"]


class TestExtractReturnType:
    """_extract_return_type checks 'return_type' and 'type' fields."""

    def test_return_type_field(self) -> None:
        rt_node = _make_node("type_identifier", "int")
        node = _make_node("function_definition", fields={"return_type": rt_node})
        result = _extract_return_type(node)
        assert result == "int"

    def test_type_field_fallback(self) -> None:
        type_node = _make_node("predefined_type", "string")
        node = _make_node("function_definition", fields={"type": type_node})
        result = _extract_return_type(node)
        assert result == "string"

    def test_no_fields_returns_none(self) -> None:
        node = _make_node("function_definition", fields={})
        result = _extract_return_type(node)
        assert result is None

    def test_overly_long_type_skipped(self) -> None:
        """Types >= 200 chars are skipped to avoid returning entire bodies."""
        long_text = "A" * 200
        type_node = _make_node("type", long_text)
        node = _make_node("function_definition", fields={"type": type_node})
        result = _extract_return_type(node)
        assert result is None


class TestExtractDocstring:
    """_extract_docstring extracts docstrings using multiple strategies."""

    def test_python_docstring(self) -> None:
        string_node = _make_node("string", '"""Hello world."""')
        expr_stmt = _make_node("expression_statement", children=[string_node])
        expr_stmt.child_count = 1
        string_node.type = "string"
        body = _make_node("block", children=[expr_stmt])
        body.child_count = 1
        node = _make_node("function_definition", children=[body])
        node.prev_named_sibling = None
        result = _extract_docstring(node, frozenset({"block"}))
        assert result == "Hello world."

    def test_no_body_returns_none(self) -> None:
        node = _make_node("function_definition", children=[])
        node.prev_named_sibling = None
        result = _extract_docstring(node, frozenset({"block"}))
        assert result is None

    def test_jsdoc_block_comment(self) -> None:
        """Strategy 2: /** ... */ preceding sibling."""
        comment = _make_node("comment", "/** Does something cool. */")
        comment.type = "comment"
        node = _make_node("function_declaration", children=[])
        node.prev_named_sibling = comment
        result = _extract_docstring(node, frozenset({"statement_block"}))
        assert result is not None
        assert "Does something cool" in result

    def test_triple_slash_comments(self) -> None:
        """Strategy 3: consecutive /// comments (C#, Rust)."""
        line2 = _make_node("comment", "/// Returns the result.")
        line2.type = "comment"
        line2.prev_named_sibling = None
        line1 = _make_node("comment", "/// Computes a value.")
        line1.type = "comment"
        line1.prev_named_sibling = None
        # line2 is immediately preceding node
        line2.prev_named_sibling = line1
        node = _make_node("function_declaration", children=[])
        node.prev_named_sibling = line2
        result = _extract_docstring(node, frozenset({"block"}))
        assert result is not None
        assert "Computes" in result

    def test_non_comment_preceding_returns_none(self) -> None:
        sibling = _make_node("expression_statement", "x = 1")
        sibling.type = "expression_statement"
        node = _make_node("function_definition", children=[])
        node.prev_named_sibling = sibling
        result = _extract_docstring(node, frozenset({"block"}))
        assert result is None
