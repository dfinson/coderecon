"""Tests for SPLADE scaffold building — identifier splitting and text generation."""

from __future__ import annotations

from unittest.mock import MagicMock
from sqlmodel import Session

from coderecon.index.search.splade_scaffold import (
    _compact_sig,
    _path_to_phrase,
    build_def_scaffold,
    build_scaffolds_for_defs,
    word_split,
)


# ── word_split ────────────────────────────────────────────────────


class TestWordSplit:
    def test_camel_case(self) -> None:
        assert word_split("parseTestOutput") == ["parse", "test", "output"]

    def test_pascal_case(self) -> None:
        assert word_split("ParseTestOutput") == ["parse", "test", "output"]

    def test_snake_case(self) -> None:
        assert word_split("parse_test_output") == ["parse", "test", "output"]

    def test_mixed_case(self) -> None:
        assert word_split("XMLParser") == ["xml", "parser"]

    def test_with_numbers(self) -> None:
        result = word_split("tier2Pack")
        assert "tier" in result
        assert "2" in result
        assert "pack" in result

    def test_single_word(self) -> None:
        assert word_split("parse") == ["parse"]

    def test_empty_string(self) -> None:
        assert word_split("") == []

    def test_underscore_only(self) -> None:
        assert word_split("_") == []

    def test_leading_underscores(self) -> None:
        assert word_split("__init__") == ["init"]

    def test_all_caps(self) -> None:
        assert word_split("HTTP") == ["http"]

    def test_caps_then_lower(self) -> None:
        result = word_split("HTTPClient")
        assert "http" in result
        assert "client" in result


# ── _path_to_phrase ───────────────────────────────────────────────


class TestPathToPhrase:
    def test_simple_path(self) -> None:
        assert _path_to_phrase("src/coderecon/testing/runner.py") == "coderecon testing runner"

    def test_strips_src_prefix(self) -> None:
        assert _path_to_phrase("src/foo/bar.py") == "foo bar"

    def test_strips_lib_prefix(self) -> None:
        assert _path_to_phrase("lib/mylib/utils.rb") == "mylib utils"

    def test_strips_app_prefix(self) -> None:
        assert _path_to_phrase("app/models/user.py") == "models user"

    def test_no_prefix(self) -> None:
        assert _path_to_phrase("coderecon/core/engine.py") == "coderecon core engine"

    def test_camel_case_segments(self) -> None:
        result = _path_to_phrase("src/MyModule/TestRunner.py")
        assert "my" in result
        assert "module" in result
        assert "test" in result
        assert "runner" in result

    def test_backslash_path(self) -> None:
        result = _path_to_phrase("src\\foo\\bar.py")
        assert "foo" in result
        assert "bar" in result

    def test_no_extension(self) -> None:
        assert _path_to_phrase("src/foo") == "foo"


# ── _compact_sig ──────────────────────────────────────────────────


class TestCompactSig:
    def test_with_params(self) -> None:
        result = _compact_sig("my_func", "(self, x: int, y: int)")
        assert "my func" in result
        assert "self" not in result
        assert "x: int" in result

    def test_self_only(self) -> None:
        result = _compact_sig("my_method", "(self)")
        # self-only → stripped to "()" which is empty-ish
        assert "my method" in result

    def test_no_sig(self) -> None:
        result = _compact_sig("my_func", "")
        assert result == "my func"

    def test_self_comma_removal(self) -> None:
        result = _compact_sig("process", "(self, data)")
        assert "self" not in result
        assert "data" in result


# ── build_def_scaffold ────────────────────────────────────────────


class TestBuildDefScaffold:
    def test_minimal_scaffold(self) -> None:
        result = build_def_scaffold("src/foo.py", kind="function", name="bar")
        assert "module foo" in result
        assert "function bar" in result

    def test_empty_name_returns_empty(self) -> None:
        assert build_def_scaffold("src/foo.py", kind="function", name="") == ""

    def test_with_signature(self) -> None:
        result = build_def_scaffold(
            "src/utils.py",
            kind="function",
            name="compute",
            signature_text="(x: int, y: int) -> int",
        )
        assert "function compute" in result
        assert "x: int" in result

    def test_with_qualified_name(self) -> None:
        result = build_def_scaffold(
            "src/mod.py",
            kind="method",
            name="run",
            qualified_name="MyClass.run",
        )
        assert "in my class" in result

    def test_with_callees(self) -> None:
        result = build_def_scaffold(
            "src/a.py",
            kind="function",
            name="process",
            callee_names=["validate", "transform", "x"],
        )
        assert "calls" in result
        assert "validate" in result
        assert "transform" in result
        # Single-char names (len < 2) are filtered out
        assert "x" not in result.split("calls")[1]

    def test_with_type_refs(self) -> None:
        result = build_def_scaffold(
            "src/a.py",
            kind="function",
            name="process",
            callee_names=["validate"],
            type_ref_names=["MyModel", "validate"],  # "validate" overlaps with callees
        )
        assert "uses" in result
        assert "MyModel" in result
        # "validate" should NOT appear in uses since it's already in callees
        uses_section = result.split("uses")[1] if "uses" in result else ""
        assert "validate" not in uses_section

    def test_with_docstring(self) -> None:
        result = build_def_scaffold(
            "src/a.py",
            kind="function",
            name="process",
            docstring="Transform input data into normalized output. Returns dict.",
        )
        assert "describes" in result
        assert "Transform input data into normalized output" in result

    def test_short_docstring_skipped(self) -> None:
        result = build_def_scaffold(
            "src/a.py",
            kind="function",
            name="process",
            docstring="Short.",
        )
        assert "describes" not in result

    def test_with_lexical_path(self) -> None:
        result = build_def_scaffold(
            "src/mod.py",
            kind="method",
            name="run",
            lexical_path="MyClass.run",
        )
        assert "in my class" in result


# ── build_scaffolds_for_defs ──────────────────────────────────────


class TestBuildScaffoldsForDefs:
    def test_empty_input(self) -> None:
        session = MagicMock(spec=Session)
        result = build_scaffolds_for_defs(session, [])
        assert result == {}

    def test_builds_scaffolds_for_batch(self) -> None:
        session = MagicMock(spec=Session)

        # Create fake DefFacts
        d1 = MagicMock()
        d1.def_uid = "uid1"
        d1.file_id = 1
        d1.name = "process"
        d1.kind = "function"
        d1.start_line = 10
        d1.end_line = 20
        d1.signature_text = "(x: int)"
        d1.qualified_name = None
        d1.lexical_path = None
        d1.docstring = None

        # Mock file lookup
        file_mock = MagicMock()
        file_mock.id = 1
        file_mock.path = "src/mymod.py"

        # Configure session.exec to return files, then refs, then target names, then annotations
        call_count = 0

        def exec_side_effect(stmt):
            nonlocal call_count
            mock_result = MagicMock()
            call_count += 1
            if call_count == 1:
                # File query
                mock_result.all.return_value = [file_mock]
            elif call_count == 2:
                # RefFact query (no refs)
                mock_result.all.return_value = []
            elif call_count == 3:
                # TypeAnnotationFact query (no annotations)
                mock_result.all.return_value = []
            else:
                mock_result.all.return_value = []
            return mock_result

        session.exec.side_effect = exec_side_effect

        result = build_scaffolds_for_defs(session, [d1])
        assert "uid1" in result
        assert "function" in result["uid1"]
        assert "process" in result["uid1"]
