"""Tests for coderecon.index._internal.indexing.semantic_resolver."""

from coderecon.index._internal.indexing.semantic_resolver import (
    TAU_REF,
    TAU_ACCESS,
    TAU_SHAPE,
    _CANDIDATE_POOL,
    _CE_BATCH,
    _batch_splade_retrieve,
)

class TestThresholdConstants:
    """Verify CE threshold constants have sensible values."""

    def test_tau_ref_is_positive(self):
        assert TAU_REF > 0

    def test_tau_access_is_positive(self):
        assert TAU_ACCESS > 0

    def test_tau_shape_is_positive(self):
        assert TAU_SHAPE > 0

    def test_access_threshold_higher_than_ref(self):
        """MemberAccess resolution is more ambiguous, so threshold should be >= RefFact."""
        assert TAU_ACCESS >= TAU_REF

    def test_candidate_pool_is_reasonable(self):
        assert 10 <= _CANDIDATE_POOL <= 200

    def test_ce_batch_is_reasonable(self):
        assert 1 <= _CE_BATCH <= 256

class TestBatchSpladeRetrieve:
    """Test _batch_splade_retrieve with empty/degenerate inputs."""

    def test_empty_queries_returns_empty(self):
        result = _batch_splade_retrieve([], {})
        assert result == []

    def test_empty_vecs_returns_empty_lists(self):
        result = _batch_splade_retrieve(["some query"], {})
        assert result == [[]]

    def test_multiple_empty_queries(self):
        result = _batch_splade_retrieve([], {"uid1": {1: 0.5}})
        assert result == []
