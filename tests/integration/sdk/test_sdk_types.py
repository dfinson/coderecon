"""Integration tests for SDK types — dataclass construction and conversion functions."""

from __future__ import annotations

import pytest

from coderecon.sdk.types import (
    CatalogEntry,
    CheckpointResult,
    CodeSpan,
    CommunitiesResult,
    CyclesResult,
    DescribeResult,
    DiffResult,
    Event,
    GraphExportResult,
    ImpactResult,
    MapResult,
    RawSignalsResult,
    ReconResult,
    RefactorCancelResult,
    RefactorCommitResult,
    RefactorResult,
    RegisterResult,
    StatusResult,
    UnderstandResult,
    _to_checkpoint_result,
    _to_communities_result,
    _to_cycles_result,
    _to_describe_result,
    _to_diff_result,
    _to_graph_export_result,
    _to_impact_result,
    _to_map_result,
    _to_raw_signals_result,
    _to_recon_result,
    _to_refactor_cancel_result,
    _to_refactor_commit_result,
    _to_refactor_result,
    _to_register_result,
    _to_status_result,
    _to_understand_result,
)

pytestmark = pytest.mark.integration


class TestCodeSpan:
    def test_frozen(self) -> None:
        cs = CodeSpan(file="a.py", start_line=1, end_line=5, content="x = 1")
        with pytest.raises(AttributeError):
            cs.file = "b.py"  # type: ignore[misc]

    def test_defaults(self) -> None:
        cs = CodeSpan(file="a.py", start_line=1, end_line=5, content="x = 1")
        assert cs.symbol is None
        assert cs.score == 0.0

    def test_with_all_fields(self) -> None:
        cs = CodeSpan(
            file="a.py", start_line=1, end_line=5, content="x = 1",
            symbol="foo", score=0.95,
        )
        assert cs.symbol == "foo"
        assert cs.score == 0.95


class TestReconResult:
    def test_defaults(self) -> None:
        r = ReconResult(recon_id="abc")
        assert r.gate == "OK"
        assert r.results == []
        assert r.metrics == {}
        assert r.hint == ""

    def test_from_wire(self) -> None:
        d = {
            "recon_id": "abc",
            "gate": "FRESH",
            "results": [{"file": "a.py"}],
            "metrics": {"elapsed": 1.2},
            "hint": "check deps",
        }
        r = _to_recon_result(d)
        assert r.recon_id == "abc"
        assert r.gate == "FRESH"
        assert len(r.results) == 1
        assert r.hint == "check deps"

    def test_from_empty_wire(self) -> None:
        r = _to_recon_result({})
        assert r.recon_id == ""


class TestMapResult:
    def test_from_wire(self) -> None:
        r = _to_map_result({"overview": "tree view", "sections": {"a": 1}})
        assert r.overview == "tree view"
        # sections is set to the entire dict (per implementation)
        assert "overview" in r.sections


class TestImpactResult:
    def test_from_wire(self) -> None:
        r = _to_impact_result({
            "references": [{"file": "a.py", "line": 10}],
            "total_references": 5,
            "files_affected": 2,
            "summary": "impacts 2 files",
        })
        assert r.total_references == 5
        assert r.files_affected == 2
        assert len(r.references) == 1


class TestRefactorResult:
    def test_from_wire(self) -> None:
        r = _to_refactor_result({
            "refactor_id": "abc",
            "status": "previewed",
            "preview": {"files_affected": 3},
        })
        assert r.refactor_id == "abc"
        assert r.status == "previewed"
        assert r.preview is not None


class TestRefactorCommitResult:
    def test_from_wire(self) -> None:
        r = _to_refactor_commit_result({
            "refactor_id": "abc",
            "status": "applied",
        })
        assert r.applied is True
        # files_modified is not populated from wire (hardcoded empty)
        assert r.files_modified == []


class TestRefactorCancelResult:
    def test_from_wire(self) -> None:
        r = _to_refactor_cancel_result({
            "refactor_id": "abc",
            "status": "cancelled",
        })
        assert r.cancelled is True


class TestDiffResult:
    def test_from_wire(self) -> None:
        r = _to_diff_result({
            "summary": "3 functions changed",
            "structural_changes": [{"type": "modified"}],
        })
        assert "3 functions" in r.summary
        assert len(r.structural_changes) == 1


class TestCyclesResult:
    def test_from_wire(self) -> None:
        r = _to_cycles_result({
            "level": "file",
            "cycles": [{"members": ["a.py", "b.py"]}],
            "summary": "1 cycle",
        })
        assert r.level == "file"
        assert len(r.cycles) == 1


class TestCommunitiesResult:
    def test_from_wire(self) -> None:
        r = _to_communities_result({
            "level": "file",
            "communities": [{"id": 0, "members": ["a.py"]}],
        })
        assert len(r.communities) == 1


class TestGraphExportResult:
    def test_from_wire(self) -> None:
        r = _to_graph_export_result({
            "path": "/tmp/graph.json",
            "message": "exported",
        })
        assert r.path == "/tmp/graph.json"


class TestCheckpointResult:
    def test_from_wire(self) -> None:
        r = _to_checkpoint_result({
            "passed": True,
            "lint": {"passed": True},
            "tests": {"passed": True, "total": 10},
            "commit": {"sha": "abc123"},
            "summary": "all good",
            "agentic_hint": "proceed",
        })
        assert r.passed is True
        assert r.lint is not None
        assert r.summary == "all good"


class TestDescribeResult:
    def test_from_wire(self) -> None:
        r = _to_describe_result({"found": True, "description": "A utility function"})
        assert r.found is True
        assert r.description == "A utility function"

    def test_not_found(self) -> None:
        r = _to_describe_result({"found": False})
        assert r.found is False


class TestRegisterResult:
    def test_from_wire(self) -> None:
        r = _to_register_result({"repo": "myrepo", "worktree": "main"})
        assert r.repo == "myrepo"
        assert r.worktree == "main"


class TestStatusResult:
    def test_from_wire(self) -> None:
        r = _to_status_result({
            "daemon_healthy": True,
            "active_repos": [{"name": "coderecon"}],
        })
        assert r.daemon_healthy is True
        assert len(r.active_repos) == 1


class TestRawSignalsResult:
    def test_from_wire(self) -> None:
        r = _to_raw_signals_result({
            "query_features": {"q": "find"},
            "repo_features": {"lang": "python"},
            "candidates": [{"file": "a.py"}],
            "diagnostics": {"elapsed": 0.5},
        })
        assert r.query_features["q"] == "find"
        assert len(r.candidates) == 1


class TestUnderstandResult:
    def test_from_wire(self) -> None:
        r = _to_understand_result({
            "sections": {"overview": "text"},
            "summary": "repo summary",
        })
        assert r.summary == "repo summary"


class TestEvent:
    def test_creation(self) -> None:
        e = Event(type="indexing.done", data={"files": 100}, ts=1234.5)
        assert e.type == "indexing.done"
        assert e.ts == 1234.5

    def test_defaults(self) -> None:
        e = Event(type="ping")
        assert e.data == {}
        assert e.ts == 0.0

    def test_frozen(self) -> None:
        e = Event(type="ping")
        with pytest.raises(AttributeError):
            e.type = "pong"  # type: ignore[misc]


class TestCatalogEntry:
    def test_defaults(self) -> None:
        ce = CatalogEntry()
        assert ce.name == ""
        assert ce.worktrees == []

    def test_with_data(self) -> None:
        ce = CatalogEntry(
            name="myrepo",
            git_dir="/path/.git",
            worktrees=[{"name": "main", "path": "/path"}],
        )
        assert ce.name == "myrepo"
        assert len(ce.worktrees) == 1
