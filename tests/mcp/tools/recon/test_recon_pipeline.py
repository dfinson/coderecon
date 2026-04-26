"""Tests for coderecon.mcp.tools.recon.pipeline."""

import pytest
from pathlib import Path

from coderecon.mcp.tools.recon.pipeline import (
    _read_snippet,
    _read_signature,
    _build_query_metrics,
    _build_hints,
    _build_ce_documents,
    _models_available,
)


class TestReadSnippet:
    """Test _read_snippet with real temp files."""

    def test_returns_none_for_missing_file(self, tmp_path):
        result = _read_snippet(tmp_path, "nonexistent.py", 1, 5)
        assert result is None

    def test_reads_correct_lines(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text("line1\nline2\nline3\nline4\nline5\n")
        result = _read_snippet(tmp_path, "sample.py", 2, 4)
        assert result == "line2\nline3\nline4"

    def test_clamps_to_file_bounds(self, tmp_path):
        f = tmp_path / "short.py"
        f.write_text("only\n")
        result = _read_snippet(tmp_path, "short.py", 1, 100)
        assert result == "only"


class TestReadSignature:
    """Test _read_signature with real temp files."""

    def test_returns_none_for_missing_file(self, tmp_path):
        result = _read_signature(tmp_path, "nonexistent.py", 1, 5)
        assert result is None

    def test_extracts_function_signature(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text('def hello(name: str) -> str:\n    """Say hello."""\n    return f"Hello {name}"\n')
        result = _read_signature(tmp_path, "mod.py", 1, 3)
        assert result is not None
        assert "def hello" in result

    def test_returns_none_for_empty_span(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        result = _read_signature(tmp_path, "empty.py", 1, 1)
        assert result is None


class TestBuildQueryMetrics:
    """Test _build_query_metrics pure function."""

    def test_empty_candidates(self):
        metrics = _build_query_metrics(
            diagnostics={"candidate_count": 0},
            candidates=[],
            seeds=None,
            pins=None,
        )
        assert metrics["total_candidates_scored"] == 0

    def test_with_candidates(self):
        candidates = [
            {"name": "foo", "path": "a.py", "score": 0.9},
            {"name": "bar", "path": "b.py", "score": 0.5},
            {"name": "baz", "path": "c.py", "score": 0.1},
        ]
        metrics = _build_query_metrics(
            diagnostics={"candidate_count": 3, "term_hits": 2, "lex_hits": 1},
            candidates=candidates,
            seeds=None,
            pins=None,
        )
        assert metrics["top_score"] == 0.9
        assert metrics["retriever_coverage"]["term_match"] == 2
        assert "score_drop_at" in metrics

    def test_seed_hit_rate(self):
        candidates = [
            {"name": "foo", "score": 0.9},
            {"name": "bar", "score": 0.5},
        ]
        metrics = _build_query_metrics(
            diagnostics={"candidate_count": 2},
            candidates=candidates,
            seeds=["foo", "missing"],
            pins=None,
        )
        assert metrics["seed_hit_rate"] == 0.5


class TestBuildHints:
    """Test _build_hints pure function."""

    def test_no_hints_when_all_good(self):
        metrics = {
            "retriever_coverage": {"lexical": 5, "term_match": 3, "symbol": 1, "graph": 2},
            "score_drop_at": 10,
        }
        hints = _build_hints(metrics, gate_label="proceed")
        assert len(hints) == 0

    def test_hint_for_no_lexical(self):
        metrics = {
            "retriever_coverage": {"lexical": 0, "term_match": 3, "symbol": 1, "graph": 1},
        }
        hints = _build_hints(metrics, gate_label="proceed")
        assert any("Lexical" in h for h in hints)

    def test_hint_for_no_term_match(self):
        metrics = {
            "retriever_coverage": {"lexical": 5, "term_match": 0, "symbol": 1, "graph": 1},
        }
        hints = _build_hints(metrics, gate_label="proceed")
        assert any("term matches" in h.lower() for h in hints)


class TestBuildCeDocuments:
    """Test _build_ce_documents pure function."""

    def test_uses_scaffold_when_available(self):
        candidates = [{"def_uid": "uid1", "path": "a.py", "kind": "function", "name": "foo"}]
        scaffolds = {"uid1": "def foo(): pass"}
        docs = _build_ce_documents(candidates, scaffolds)
        assert docs == ["def foo(): pass"]

    def test_falls_back_to_metadata(self):
        candidates = [{"def_uid": "uid1", "path": "a.py", "kind": "function", "name": "foo"}]
        scaffolds = {}
        docs = _build_ce_documents(candidates, scaffolds)
        assert len(docs) == 1
        assert "a.py" in docs[0]
        assert "function foo" in docs[0]

    def test_empty_candidates(self):
        assert _build_ce_documents([], {}) == []


class TestModelsAvailable:
    """Test _models_available returns a boolean."""

    def test_returns_bool(self):
        result = _models_available()
        assert isinstance(result, bool)
