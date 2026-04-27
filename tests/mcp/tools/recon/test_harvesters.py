"""Tests for coderecon.mcp.tools.recon.harvesters."""

from coderecon.mcp.tools.recon.graph_harvester import _harvest_graph
from coderecon.mcp.tools.recon.harvesters import (
    _harvest_term_match,
    _harvest_explicit,
    _harvest_imports,
    _harvest_splade,
)
from coderecon.mcp.tools.recon.models import (
    EvidenceRecord,
    HarvestCandidate,
    ParsedTask,
    TaskIntent,
)


class TestHarvesterImports:
    """Verify all harvester functions are importable and are coroutines."""

    def test_harvest_term_match_is_coroutine(self):
        import asyncio

        assert asyncio.iscoroutinefunction(_harvest_term_match)

    def test_harvest_explicit_is_coroutine(self):
        import asyncio

        assert asyncio.iscoroutinefunction(_harvest_explicit)

    def test_harvest_graph_is_coroutine(self):
        import asyncio

        assert asyncio.iscoroutinefunction(_harvest_graph)

    def test_harvest_imports_is_coroutine(self):
        import asyncio

        assert asyncio.iscoroutinefunction(_harvest_imports)

    def test_harvest_splade_is_coroutine(self):
        import asyncio

        assert asyncio.iscoroutinefunction(_harvest_splade)


class TestHarvestCandidateIntegration:
    """Test HarvestCandidate used by harvesters."""

    def test_candidate_with_evidence(self):
        ev = EvidenceRecord(category="term_match", detail="matched 'foo'", score=1.0)
        cand = HarvestCandidate(
            def_uid="test::func",
            from_term_match=True,
            term_match_count=1,
            evidence=[ev],
        )
        assert cand.evidence_axes == 1
        assert cand.from_term_match is True
        assert cand.evidence[0].category == "term_match"

    def test_candidate_multiple_axes(self):
        cand = HarvestCandidate(
            def_uid="test::cls",
            from_term_match=True,
            from_graph=True,
            from_explicit=True,
        )
        assert cand.evidence_axes == 3

    def test_parsed_task_for_harvesters(self):
        task = ParsedTask(
            raw="Fix the broken import in coordinator.py",
            intent=TaskIntent.debug,
            primary_terms=["coordinator", "import"],
            explicit_paths=["src/coordinator.py"],
        )
        assert task.intent == TaskIntent.debug
        assert len(task.primary_terms) == 2
        assert task.explicit_paths == ["src/coordinator.py"]
