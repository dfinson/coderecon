"""Tests for coderecon.sdk.types — wire → typed conversion."""

from __future__ import annotations

from coderecon.sdk.types import (
    MapResult,
    ReconResult,
    _to_checkpoint_result,
    _to_communities_result,
    _to_cycles_result,
    _to_describe_result,
    _to_diff_result,
    _to_graph_export_result,
    _to_impact_result,
    _to_map_result,
    _to_recon_result,
    _to_refactor_cancel_result,
    _to_refactor_commit_result,
    _to_refactor_result,
    _to_register_result,
    _to_status_result,
    _to_understand_result,
)

class TestReconResult:
    def test_full(self) -> None:
        wire = {"recon_id": "abc", "gate": "PARTIAL", "results": [{"a": 1}], "metrics": {"t": 0.5}, "hint": "look here"}
        r = _to_recon_result(wire)
        assert isinstance(r, ReconResult)
        assert r.recon_id == "abc"
        assert r.gate == "PARTIAL"
        assert r.results == [{"a": 1}]
        assert r.metrics["t"] == 0.5
        assert r.hint == "look here"
        assert r.raw is wire

    def test_defaults(self) -> None:
        r = _to_recon_result({})
        assert r.recon_id == ""
        assert r.gate == "OK"
        assert r.results == []

class TestMapResult:
    def test_basic(self) -> None:
        wire = {"overview": "Python repo", "languages": ["py"]}
        r = _to_map_result(wire)
        assert isinstance(r, MapResult)
        assert r.overview == "Python repo"
        assert r.raw is wire

class TestImpactResult:
    def test_basic(self) -> None:
        wire = {"references": [{"file": "a.py"}], "total_references": 5, "files_affected": 2, "summary": "done"}
        r = _to_impact_result(wire)
        assert r.total_references == 5
        assert r.files_affected == 2
        assert len(r.references) == 1

class TestUnderstandResult:
    def test_basic(self) -> None:
        r = _to_understand_result({"summary": "hello", "sections": {"a": "b"}})
        assert r.summary == "hello"
        assert r.sections == {"a": "b"}

class TestRefactorResult:
    def test_basic(self) -> None:
        r = _to_refactor_result({"refactor_id": "r1", "status": "previewed", "agentic_hint": "check"})
        assert r.refactor_id == "r1"
        assert r.status == "previewed"

class TestRefactorCommitResult:
    def test_applied(self) -> None:
        r = _to_refactor_commit_result({"refactor_id": "r1", "status": "applied"})
        assert r.applied is True

    def test_not_applied(self) -> None:
        r = _to_refactor_commit_result({"refactor_id": "r1", "status": "inspected"})
        assert r.applied is False

class TestRefactorCancelResult:
    def test_cancelled(self) -> None:
        r = _to_refactor_cancel_result({"refactor_id": "r1", "status": "cancelled"})
        assert r.cancelled is True

    def test_not_cancelled(self) -> None:
        r = _to_refactor_cancel_result({"refactor_id": "r1", "status": "not_found"})
        assert r.cancelled is False

class TestDiffResult:
    def test_basic(self) -> None:
        r = _to_diff_result({"summary": "2 files changed", "structural_changes": [{"kind": "add"}]})
        assert r.summary == "2 files changed"
        assert len(r.structural_changes) == 1

class TestCyclesResult:
    def test_basic(self) -> None:
        r = _to_cycles_result({"level": "def", "cycles": [["a", "b"]], "summary": "1 cycle"})
        assert r.level == "def"
        assert len(r.cycles) == 1

class TestCommunitiesResult:
    def test_basic(self) -> None:
        r = _to_communities_result({"level": "file", "communities": [{"name": "c1"}]})
        assert len(r.communities) == 1

class TestGraphExportResult:
    def test_basic(self) -> None:
        r = _to_graph_export_result({"path": "/tmp/out.json", "message": "ok"})
        assert r.path == "/tmp/out.json"

class TestCheckpointResult:
    def test_passed(self) -> None:
        r = _to_checkpoint_result({"passed": True, "summary": "all ok", "agentic_hint": "commit"})
        assert r.passed is True
        assert r.agentic_hint == "commit"

class TestDescribeResult:
    def test_found(self) -> None:
        r = _to_describe_result({"found": True, "description": "does x"})
        assert r.found is True
        assert r.description == "does x"

class TestRegisterResult:
    def test_basic(self) -> None:
        r = _to_register_result({"repo": "myrepo", "worktree": "main"})
        assert r.repo == "myrepo"
        assert r.worktree == "main"

class TestStatusResult:
    def test_basic(self) -> None:
        r = _to_status_result({"daemon_healthy": True, "active_repos": [{"name": "r"}]})
        assert r.daemon_healthy is True
        assert len(r.active_repos) == 1
