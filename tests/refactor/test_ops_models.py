"""Tests for refactor ops_models dataclasses and pure functions."""

from __future__ import annotations

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


class TestEditHunk:
    def test_fields(self) -> None:
        h = EditHunk(old="foo", new="bar", line=10, certainty="high")
        assert h.old == "foo"
        assert h.new == "bar"
        assert h.line == 10
        assert h.certainty == "high"


class TestFileEdit:
    def test_default_hunks(self) -> None:
        fe = FileEdit(path="src/foo.py")
        assert fe.hunks == []

    def test_with_hunks(self) -> None:
        h = EditHunk("a", "b", 1, "low")
        fe = FileEdit(path="x.py", hunks=[h])
        assert len(fe.hunks) == 1


class TestRefactorPreview:
    def test_defaults(self) -> None:
        p = RefactorPreview(files_affected=0)
        assert p.edits == []
        assert p.high_certainty_count == 0
        assert p.verification_required is False
        assert p.move_from is None

    def test_move_metadata(self) -> None:
        p = RefactorPreview(
            files_affected=1, move_from="old.py", move_to="new.py"
        )
        assert p.move_from == "old.py"
        assert p.move_to == "new.py"


class TestRefactorResult:
    def test_previewed(self) -> None:
        r = RefactorResult(
            refactor_id="r1",
            status="previewed",
            preview=RefactorPreview(files_affected=2),
        )
        assert r.status == "previewed"
        assert r.preview is not None
        assert r.applied is None

    def test_cancelled(self) -> None:
        r = RefactorResult(refactor_id="r2", status="cancelled")
        assert r.changed_paths == []


class TestRefactorDivergence:
    def test_defaults(self) -> None:
        d = RefactorDivergence()
        assert d.conflicting_hunks == []
        assert d.resolution_options == []


class TestInspectResult:
    def test_fields(self) -> None:
        r = InspectResult(path="foo.py", matches=[{"line": 1, "snippet": "x"}])
        assert r.path == "foo.py"
        assert len(r.matches) == 1


class TestWordBoundaryMatch:
    def test_match(self) -> None:
        assert _word_boundary_match("def foo():", "foo") is True

    def test_no_match_substring(self) -> None:
        assert _word_boundary_match("foobar", "foo") is False

    def test_match_in_comment(self) -> None:
        assert _word_boundary_match("# rename foo here", "foo") is True

    def test_no_match_empty(self) -> None:
        assert _word_boundary_match("", "foo") is False


class TestScanFileForCommentOccurrences:
    def test_python_hash_comment(self) -> None:
        content = "x = 1\n# TODO: rename foo\ny = 2"
        result = _scan_file_for_comment_occurrences(content, "foo", "python")
        assert len(result) == 1
        assert result[0][0] == 2  # line number

    def test_python_docstring(self) -> None:
        content = '"""\nThis mentions foo.\n"""\nx = 1'
        result = _scan_file_for_comment_occurrences(content, "foo", "python")
        assert len(result) == 1
        assert result[0][0] == 2

    def test_python_no_match(self) -> None:
        content = "x = foo()\n# no mention of target"
        result = _scan_file_for_comment_occurrences(content, "bar", "python")
        assert result == []

    def test_javascript_line_comment(self) -> None:
        content = "const x = 1;\n// rename foo here\nconst y = 2;"
        result = _scan_file_for_comment_occurrences(content, "foo", "javascript")
        assert len(result) == 1
        assert result[0][0] == 2

    def test_javascript_block_comment(self) -> None:
        content = "/* foo is deprecated */\nconst x = 1;"
        result = _scan_file_for_comment_occurrences(content, "foo", "javascript")
        assert len(result) == 1

    def test_no_language_defaults_python(self) -> None:
        content = "# mentions foo"
        result = _scan_file_for_comment_occurrences(content, "foo", None)
        assert len(result) == 1
