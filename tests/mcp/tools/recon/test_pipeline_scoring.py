"""Tests for pipeline_scoring — snippet reading and CE document building."""

from __future__ import annotations

from pathlib import Path

from coderecon.mcp.tools.recon.pipeline_scoring import (
    _build_ce_documents,
    _read_signature,
    _read_snippet,
)


class TestReadSnippet:
    def test_reads_line_range(self, tmp_path: Path) -> None:
        f = tmp_path / "sample.py"
        f.write_text("line1\nline2\nline3\nline4\nline5\n")
        result = _read_snippet(tmp_path, "sample.py", 2, 4)
        assert result == "line2\nline3\nline4"

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        result = _read_snippet(tmp_path, "nonexistent.py", 1, 5)
        assert result is None

    def test_clamps_to_file_bounds(self, tmp_path: Path) -> None:
        f = tmp_path / "short.py"
        f.write_text("only\ntwo\n")
        # Request lines 1–10 on a 2-line file → returns both lines
        result = _read_snippet(tmp_path, "short.py", 1, 10)
        assert result == "only\ntwo"

    def test_start_line_is_one_indexed(self, tmp_path: Path) -> None:
        f = tmp_path / "indexed.py"
        f.write_text("a\nb\nc\n")
        result = _read_snippet(tmp_path, "indexed.py", 1, 1)
        assert result == "a"

    def test_handles_unicode(self, tmp_path: Path) -> None:
        f = tmp_path / "uni.py"
        f.write_text("café\nnaïve\n", encoding="utf-8")
        result = _read_snippet(tmp_path, "uni.py", 1, 2)
        assert result == "café\nnaïve"

    def test_subdirectory_path(self, tmp_path: Path) -> None:
        sub = tmp_path / "src" / "pkg"
        sub.mkdir(parents=True)
        f = sub / "mod.py"
        f.write_text("hello\nworld\n")
        result = _read_snippet(tmp_path, "src/pkg/mod.py", 1, 1)
        assert result == "hello"


class TestReadSignature:
    def test_simple_function(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text('def foo(x: int) -> str:\n    """Do foo."""\n    return str(x)\n')
        result = _read_signature(tmp_path, "mod.py", 1, 3)
        assert "def foo(x: int) -> str:" in result
        assert '"""Do foo."""' in result

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        result = _read_signature(tmp_path, "gone.py", 1, 5)
        assert result is None

    def test_returns_none_for_empty_span(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.py"
        f.write_text("")
        result = _read_signature(tmp_path, "empty.py", 5, 10)
        assert result is None

    def test_multiline_signature_with_continuation(self, tmp_path: Path) -> None:
        f = tmp_path / "multi.py"
        content = (
            "def bar(\n"
            "    a: int,\n"
            "    b: str,\n"
            ") -> None:\n"
            '    """Bar docs."""\n'
            "    pass\n"
        )
        f.write_text(content)
        result = _read_signature(tmp_path, "multi.py", 1, 6)
        assert result is not None
        assert "def bar(" in result

    def test_no_docstring(self, tmp_path: Path) -> None:
        f = tmp_path / "nodoc.py"
        f.write_text("def baz() -> None:\n    pass\n")
        result = _read_signature(tmp_path, "nodoc.py", 1, 2)
        assert result is not None
        assert "def baz() -> None:" in result


class TestBuildCeDocuments:
    def test_uses_scaffold_when_available(self) -> None:
        candidates = [
            {"def_uid": "uid1", "path": "a.py", "kind": "function", "name": "foo"},
        ]
        scaffolds = {"uid1": "scaffold text for foo"}
        docs = _build_ce_documents(candidates, scaffolds)
        assert docs == ["scaffold text for foo"]

    def test_falls_back_to_metadata(self) -> None:
        candidates = [
            {"def_uid": "uid1", "path": "src/bar.py", "kind": "class", "name": "Bar"},
        ]
        scaffolds = {}  # No scaffold available
        docs = _build_ce_documents(candidates, scaffolds)
        assert len(docs) == 1
        assert "src/bar.py" in docs[0]
        assert "class Bar" in docs[0]

    def test_mixed_scaffold_and_fallback(self) -> None:
        candidates = [
            {"def_uid": "a", "path": "x.py", "kind": "function", "name": "x"},
            {"def_uid": "b", "path": "y.py", "kind": "class", "name": "Y"},
        ]
        scaffolds = {"a": "scaffold A"}
        docs = _build_ce_documents(candidates, scaffolds)
        assert docs[0] == "scaffold A"
        assert "y.py" in docs[1]

    def test_empty_candidates(self) -> None:
        docs = _build_ce_documents([], {})
        assert docs == []

    def test_missing_def_uid(self) -> None:
        candidates = [{"path": "z.py", "kind": "function", "name": "z"}]
        scaffolds = {}
        docs = _build_ce_documents(candidates, scaffolds)
        assert len(docs) == 1
        assert "z.py" in docs[0]

    def test_missing_fields_produce_empty_strings(self) -> None:
        candidates = [{"def_uid": "u", "path": "", "kind": "", "name": ""}]
        scaffolds = {}
        docs = _build_ce_documents(candidates, scaffolds)
        assert len(docs) == 1
        # Should still produce a string, not crash
        assert isinstance(docs[0], str)
