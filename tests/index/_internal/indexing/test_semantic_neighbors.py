"""Tests for coderecon.index._internal.indexing.semantic_neighbors."""

import pytest

from coderecon.index._internal.indexing.semantic_neighbors import (
    SIGMA_FLOOR,
    MAX_NEIGHBORS_PER_DEF,
    compute_semantic_neighbors,
)


class TestConstants:
    """Verify tuning constants have sensible values."""

    def test_sigma_floor_is_positive(self):
        assert SIGMA_FLOOR > 0

    def test_max_neighbors_is_bounded(self):
        assert 1 <= MAX_NEIGHBORS_PER_DEF <= 100


class TestComputeSemanticNeighborsSignature:
    """Verify the function signature and default parameters."""

    def test_function_accepts_expected_kwargs(self):
        """Ensure the function signature hasn't drifted."""
        import inspect

        sig = inspect.signature(compute_semantic_neighbors)
        param_names = set(sig.parameters.keys())
        assert "db" in param_names
        assert "sigma_floor" in param_names
        assert "max_per_def" in param_names
        assert "block_size" in param_names
        assert "changed_file_ids" in param_names

    def test_default_sigma_floor_matches_constant(self):
        import inspect

        sig = inspect.signature(compute_semantic_neighbors)
        assert sig.parameters["sigma_floor"].default == SIGMA_FLOOR

    def test_default_max_per_def_matches_constant(self):
        import inspect

        sig = inspect.signature(compute_semantic_neighbors)
        assert sig.parameters["max_per_def"].default == MAX_NEIGHBORS_PER_DEF
