"""Tests for treesitter_models dataclasses."""

from __future__ import annotations

from unittest.mock import MagicMock

from coderecon.index.parsing.treesitter_models import (
    DynamicAccess,
    IdentifierOccurrence,
    ParseResult,
    ProbeValidation,
    SyntacticBind,
    SyntacticImport,
    SyntacticScope,
    SyntacticSymbol,
    _CSHARP_PREPROC_WRAPPERS,
    _import_uid,
)


class TestSyntacticScope:
    def test_fields(self) -> None:
        s = SyntacticScope(
            scope_id=0,
            parent_scope_id=None,
            kind="file",
            start_line=1,
            start_col=0,
            end_line=100,
            end_col=0,
        )
        assert s.scope_id == 0
        assert s.parent_scope_id is None
        assert s.kind == "file"

    def test_nested_scope(self) -> None:
        s = SyntacticScope(1, 0, "function", 10, 4, 20, 0)
        assert s.parent_scope_id == 0
        assert s.kind == "function"


class TestSyntacticImport:
    def test_fields(self) -> None:
        imp = SyntacticImport(
            import_uid="abc123",
            imported_name="os.path",
            alias=None,
            source_literal="os",
            import_kind="python_import",
            start_line=1,
            start_col=0,
            end_line=1,
            end_col=9,
        )
        assert imp.imported_name == "os.path"
        assert imp.alias is None
        assert imp.scope_id is None

    def test_with_alias(self) -> None:
        imp = SyntacticImport(
            import_uid="x",
            imported_name="numpy",
            alias="np",
            source_literal="numpy",
            import_kind="python_import",
            start_line=1,
            start_col=0,
            end_line=1,
            end_col=20,
            scope_id=0,
        )
        assert imp.alias == "np"
        assert imp.scope_id == 0


class TestSyntacticBind:
    def test_fields(self) -> None:
        b = SyntacticBind(
            name="x",
            scope_id=1,
            target_kind="DEF",
            target_uid="def_123",
            reason_code="LOCAL_ASSIGN",
            start_line=5,
            start_col=4,
        )
        assert b.name == "x"
        assert b.target_kind == "DEF"


class TestDynamicAccess:
    def test_defaults(self) -> None:
        d = DynamicAccess(pattern_type="bracket_access", start_line=10, start_col=0)
        assert d.extracted_literals == []
        assert d.has_non_literal_key is False

    def test_with_literals(self) -> None:
        d = DynamicAccess(
            pattern_type="getattr",
            start_line=1,
            start_col=0,
            extracted_literals=["foo", "bar"],
            has_non_literal_key=True,
        )
        assert len(d.extracted_literals) == 2
        assert d.has_non_literal_key is True


class TestSyntacticSymbol:
    def test_minimal(self) -> None:
        s = SyntacticSymbol(
            name="my_func", kind="function", line=1, column=0, end_line=5, end_column=0
        )
        assert s.name == "my_func"
        assert s.signature is None
        assert s.parent_name is None

    def test_method_with_parent(self) -> None:
        s = SyntacticSymbol(
            name="do_thing",
            kind="method",
            line=10,
            column=4,
            end_line=20,
            end_column=0,
            parent_name="MyClass",
            signature_text="(self, x: int)",
            return_type="bool",
        )
        assert s.parent_name == "MyClass"
        assert s.return_type == "bool"


class TestIdentifierOccurrence:
    def test_fields(self) -> None:
        o = IdentifierOccurrence(name="foo", line=1, column=0, end_line=1, end_column=3)
        assert o.name == "foo"


class TestProbeValidation:
    def test_valid(self) -> None:
        p = ProbeValidation(
            is_valid=True,
            error_count=0,
            total_nodes=100,
            has_meaningful_content=True,
        )
        assert p.is_valid is True
        assert p.error_ratio == 0.0

    def test_invalid(self) -> None:
        p = ProbeValidation(
            is_valid=False,
            error_count=50,
            total_nodes=100,
            has_meaningful_content=False,
            error_ratio=0.5,
        )
        assert p.error_ratio == 0.5


class TestCSharpPreprocWrappers:
    def test_contains_expected(self) -> None:
        assert "preproc_if" in _CSHARP_PREPROC_WRAPPERS
        assert "preproc_region" in _CSHARP_PREPROC_WRAPPERS

    def test_is_frozenset(self) -> None:
        assert isinstance(_CSHARP_PREPROC_WRAPPERS, frozenset)


class TestImportUid:
    def test_deterministic(self) -> None:
        uid1 = _import_uid("file.py", "os", 1)
        uid2 = _import_uid("file.py", "os", 1)
        assert uid1 == uid2

    def test_different_inputs(self) -> None:
        uid1 = _import_uid("a.py", "os", 1)
        uid2 = _import_uid("b.py", "os", 1)
        assert uid1 != uid2

    def test_length(self) -> None:
        uid = _import_uid("file.py", "os", 1)
        assert len(uid) == 16

    def test_different_line_numbers(self) -> None:
        uid1 = _import_uid("file.py", "os", 1)
        uid2 = _import_uid("file.py", "os", 2)
        assert uid1 != uid2

    def test_hex_output(self) -> None:
        uid = _import_uid("file.py", "os", 1)
        int(uid, 16)  # must be valid hex — raises ValueError if not


class TestSyntacticSymbolAllFields:
    """Test SyntacticSymbol with all optional fields populated."""

    def test_decorators_and_docstring(self) -> None:
        s = SyntacticSymbol(
            name="handler",
            kind="method",
            line=5,
            column=4,
            end_line=15,
            end_column=0,
            signature="(self, request: Request) -> Response",
            parent_name="MyView",
            signature_text="(self, request: Request)",
            decorators=["@login_required", "@cache"],
            docstring="Handle incoming request.",
            return_type="Response",
        )
        assert s.decorators == ["@login_required", "@cache"]
        assert s.docstring == "Handle incoming request."
        assert s.signature is not None

    def test_empty_decorators_list(self) -> None:
        s = SyntacticSymbol(
            name="f", kind="function", line=1, column=0,
            end_line=2, end_column=0, decorators=[],
        )
        assert s.decorators == []


class TestDynamicAccessFieldSafety:
    """Ensure default-factory list is independent per instance."""

    def test_independent_default_lists(self) -> None:
        a = DynamicAccess(pattern_type="eval", start_line=1, start_col=0)
        b = DynamicAccess(pattern_type="eval", start_line=2, start_col=0)
        a.extracted_literals.append("x")
        assert b.extracted_literals == []


class TestParseResult:
    """Test ParseResult construction with mocked tree-sitter objects."""

    def test_construction(self) -> None:
        tree = MagicMock()
        root = MagicMock()
        pr = ParseResult(
            tree=tree,
            language="python",
            error_count=0,
            total_nodes=50,
            root_node=root,
        )
        assert pr.language == "python"
        assert pr.error_count == 0
        assert pr.total_nodes == 50
        assert pr.ts_language is None
        assert pr.tree is tree
        assert pr.root_node is root

    def test_with_ts_language(self) -> None:
        tree = MagicMock()
        root = MagicMock()
        lang = MagicMock()
        pr = ParseResult(
            tree=tree,
            language="javascript",
            error_count=2,
            total_nodes=100,
            root_node=root,
            ts_language=lang,
        )
        assert pr.ts_language is lang
        assert pr.error_count == 2
