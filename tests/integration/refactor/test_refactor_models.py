"""Integration tests for refactor models — comment scanning, word boundary, certainty."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest

from coderecon.refactor.ops_models import (
    EditHunk,
    FileEdit,
    InspectResult,
    RefactorDivergence,
    RefactorPreview,
    RefactorResult,
    _scan_file_for_comment_occurrences,
    _word_boundary_match,
)

pytestmark = pytest.mark.integration


class TestWordBoundaryMatch:
    def test_exact_match(self) -> None:
        assert _word_boundary_match("def foo():", "foo") is True

    def test_no_match(self) -> None:
        assert _word_boundary_match("def bar():", "foo") is False

    def test_substring_not_matched(self) -> None:
        """'foo' should not match 'foobar'."""
        assert _word_boundary_match("def foobar():", "foo") is False

    def test_prefix_not_matched(self) -> None:
        assert _word_boundary_match("def barfoo():", "foo") is False

    def test_underscore_boundary(self) -> None:
        """Underscores are word chars — 'foo' should not match '_foo_bar'."""
        assert _word_boundary_match("_foo_bar", "foo") is False

    def test_multiple_occurrences(self) -> None:
        assert _word_boundary_match("foo = foo + 1", "foo") is True

    def test_in_comment(self) -> None:
        assert _word_boundary_match("# The foo variable", "foo") is True

    def test_symbol_with_special_chars_escaped(self) -> None:
        """Regex special chars in symbol name should be escaped properly."""
        # re.escape makes . match literal dot, so a.b matches literal "a.b"
        assert _word_boundary_match("x = a.b", "a.b") is True
        # But a.b should NOT match "aXb" since dot is escaped
        assert _word_boundary_match("x = aXb", "a.b") is False

    def test_empty_symbol(self) -> None:
        # Edge case: empty string matches everywhere
        assert _word_boundary_match("anything", "") is True

    def test_empty_text(self) -> None:
        assert _word_boundary_match("", "foo") is False

    def test_camelcase_symbol(self) -> None:
        assert _word_boundary_match("result = MyClass()", "MyClass") is True
        assert _word_boundary_match("result = MyClassExtra()", "MyClass") is False


class TestScanFileForCommentOccurrences:
    def test_python_hash_comment(self) -> None:
        content = "x = 1\n# The Calculator class does stuff\ny = 2\n"
        hits = _scan_file_for_comment_occurrences(content, "Calculator", "python")
        assert len(hits) == 1
        assert hits[0][0] == 2  # line number

    def test_python_docstring(self) -> None:
        content = '"""This uses Calculator to compute."""\nx = 1\n'
        hits = _scan_file_for_comment_occurrences(content, "Calculator", "python")
        assert len(hits) == 1
        assert hits[0][0] == 1

    def test_python_multiline_docstring(self) -> None:
        content = '"""\nThis module provides Calculator.\n"""\nx = 1\n'
        hits = _scan_file_for_comment_occurrences(content, "Calculator", "python")
        assert len(hits) == 1
        assert hits[0][0] == 2

    def test_python_single_quote_docstring(self) -> None:
        content = "'''\nCalculator is great.\n'''\n"
        hits = _scan_file_for_comment_occurrences(content, "Calculator", "python")
        assert len(hits) == 1

    def test_python_no_match_in_code(self) -> None:
        """Should only find occurrences in comments/docstrings, not code."""
        content = "Calculator = 1\n"
        hits = _scan_file_for_comment_occurrences(content, "Calculator", "python")
        assert len(hits) == 0

    def test_javascript_line_comment(self) -> None:
        content = "const x = 1;\n// The Calculator is used here\nconst y = 2;\n"
        hits = _scan_file_for_comment_occurrences(content, "Calculator", "javascript")
        assert len(hits) == 1
        assert hits[0][0] == 2

    def test_javascript_block_comment(self) -> None:
        content = "/* Calculator docs\n * More text about Calculator\n */\n"
        hits = _scan_file_for_comment_occurrences(content, "Calculator", "javascript")
        assert len(hits) == 2

    def test_typescript_jsdoc(self) -> None:
        content = "/** @param {Calculator} calc */\nfunction run(calc) {}\n"
        hits = _scan_file_for_comment_occurrences(content, "Calculator", "typescript")
        assert len(hits) == 1

    def test_no_comments_no_hits(self) -> None:
        content = "x = Calculator()\ny = x.add(1)\n"
        hits = _scan_file_for_comment_occurrences(content, "Calculator", "python")
        assert len(hits) == 0

    def test_none_language_defaults_to_python(self) -> None:
        content = "# Calculator comment\n"
        hits = _scan_file_for_comment_occurrences(content, "Calculator", None)
        assert len(hits) == 1

    def test_unsupported_language_returns_empty(self) -> None:
        content = "# Calculator comment\n"
        hits = _scan_file_for_comment_occurrences(content, "Calculator", "haskell")
        assert len(hits) == 0

    def test_empty_content(self) -> None:
        hits = _scan_file_for_comment_occurrences("", "Calculator", "python")
        assert len(hits) == 0


class TestEditHunk:
    def test_dataclass_fields(self) -> None:
        hunk = EditHunk(old="foo", new="bar", line=10, certainty="high")
        assert hunk.old == "foo"
        assert hunk.new == "bar"
        assert hunk.line == 10
        assert hunk.certainty == "high"


class TestFileEdit:
    def test_default_hunks_empty(self) -> None:
        fe = FileEdit(path="src/main.py")
        assert fe.hunks == []

    def test_with_hunks(self) -> None:
        hunks = [EditHunk(old="a", new="b", line=1, certainty="high")]
        fe = FileEdit(path="src/main.py", hunks=hunks)
        assert len(fe.hunks) == 1


class TestRefactorPreview:
    def test_defaults(self) -> None:
        p = RefactorPreview(files_affected=0)
        assert p.edits == []
        assert p.high_certainty_count == 0
        assert p.medium_certainty_count == 0
        assert p.low_certainty_count == 0
        assert p.verification_required is False
        assert p.verification_guidance is None
        assert p.move_from is None
        assert p.move_to is None

    def test_with_counts(self) -> None:
        p = RefactorPreview(
            files_affected=3,
            high_certainty_count=5,
            medium_certainty_count=2,
            low_certainty_count=1,
            verification_required=True,
            low_certainty_files=["a.py"],
        )
        assert p.files_affected == 3
        assert p.verification_required is True


class TestRefactorResult:
    def test_previewed_status(self) -> None:
        r = RefactorResult(
            refactor_id="abc123",
            status="previewed",
            preview=RefactorPreview(files_affected=2),
        )
        assert r.status == "previewed"
        assert r.preview is not None

    def test_cancelled_status(self) -> None:
        r = RefactorResult(refactor_id="abc", status="cancelled")
        assert r.preview is None
        assert r.applied is None

    def test_divergence_status(self) -> None:
        div = RefactorDivergence(
            conflicting_hunks=[{"file": "a.py", "lines": ["1", "2"]}],
            resolution_options=["manual"],
        )
        r = RefactorResult(refactor_id="abc", status="divergence", divergence=div)
        assert r.divergence is not None
        assert len(r.divergence.conflicting_hunks) == 1

    def test_warning_field(self) -> None:
        r = RefactorResult(
            refactor_id="abc",
            status="previewed",
            warning="Detected path:line format",
        )
        assert r.warning is not None


class TestInspectResult:
    def test_creation(self) -> None:
        ir = InspectResult(
            path="src/main.py",
            matches=[{"line": 10, "snippet": "foo = bar"}],
        )
        assert ir.path == "src/main.py"
        assert len(ir.matches) == 1
