"""Tests for new raw signal features (signal gap implementation).

Covers:
- Locality helpers (_min_path_distance, _min_package_distance)
- New ranker features (graph_caller_tier, is_endpoint, etc.)
- Cutoff features (score_entropy, cumulative_mass_top10)
"""

from __future__ import annotations

import pytest

from coderecon.mcp.tools.recon.raw_signals import (
    _min_package_distance,
    _min_path_distance,
    _shared_prefix_depth,
)
from coderecon.ranking.features import extract_cutoff_features, extract_ranker_features


# ===================================================================
# Locality helpers
# ===================================================================


class TestSharedPrefixDepth:
    def test_same_directory(self):
        assert _shared_prefix_depth("src/auth/login.py", "src/auth/logout.py") == 2

    def test_different_roots(self):
        assert _shared_prefix_depth("src/main.py", "tests/main.py") == 0

    def test_nested_shared(self):
        assert _shared_prefix_depth("a/b/c/d.py", "a/b/x/y.py") == 2

    def test_identical(self):
        assert _shared_prefix_depth("a/b/c.py", "a/b/c.py") == 3

    def test_empty(self):
        assert _shared_prefix_depth("", "a/b.py") == 0


class TestMinPathDistance:
    def test_same_directory(self):
        d = _min_path_distance("src/auth/login.py", ["src/auth/logout.py"])
        assert d == 0  # same dir: (2-2)+(2-2)=0

    def test_sibling_directories(self):
        d = _min_path_distance("src/auth/login.py", ["src/billing/charge.py"])
        assert d == 2  # (2-1)+(2-1)=2

    def test_no_seeds(self):
        assert _min_path_distance("src/main.py", []) == 999

    def test_multiple_seeds_picks_closest(self):
        d = _min_path_distance(
            "src/auth/login.py",
            ["tests/test_auth.py", "src/auth/utils.py"],
        )
        assert d == 0  # src/auth matches exactly


class TestMinPackageDistance:
    def test_same_module(self):
        same, dist = _min_package_distance("coderecon.auth", ["coderecon.auth"])
        assert same is True
        assert dist == 0

    def test_sibling_modules(self):
        same, dist = _min_package_distance("coderecon.auth", ["coderecon.billing"])
        assert same is False
        assert dist == 2

    def test_parent_child(self):
        same, dist = _min_package_distance(
            "coderecon.auth.jwt", ["coderecon.auth"]
        )
        assert same is True
        assert dist == 1

    def test_no_seeds(self):
        same, dist = _min_package_distance("coderecon.auth", [])
        assert same is False
        assert dist == 999

    def test_empty_module(self):
        same, dist = _min_package_distance("", ["coderecon.auth"])
        assert same is False
        assert dist == 999


# ===================================================================
# Ranker features
# ===================================================================


class TestNewRankerFeatures:
    """Verify new signal features appear in extract_ranker_features output."""

    def _make_candidate(self, **overrides):
        base = {
            "term_match_count": 1,
            "term_total_matches": 10,
            "lex_hit_count": 2,
            "graph_edge_type": "caller",
            "graph_seed_rank": 1,
            "graph_caller_max_tier": "proven",
            "symbol_source": None,
            "import_direction": None,
            "retriever_hits": 2,
            "object_size_lines": 20,
            "path_depth": 3,
            "nesting_depth": 1,
            "hub_score": 5,
            "is_test": False,
            "is_endpoint": True,
            "test_coverage_count": 3,
            "has_docstring": True,
            "has_decorators": False,
            "has_return_type": True,
            "has_parent_scope": True,
            "signature_text": "(self, x: int)",
            "seed_path_distance": 1,
            "same_package": True,
            "package_distance": 0,
        }
        base.update(overrides)
        return base

    def test_graph_caller_tier_proven(self):
        query_f = {"query_len": 10, "has_identifier": True, "has_path": False,
                    "identifier_density": 0.5, "term_count": 3}
        feats = extract_ranker_features([self._make_candidate()], query_f)
        assert feats[0]["graph_caller_tier"] == 3  # proven

    def test_graph_caller_tier_none(self):
        query_f = {"query_len": 10, "has_identifier": True, "has_path": False,
                    "identifier_density": 0.5, "term_count": 3}
        feats = extract_ranker_features(
            [self._make_candidate(graph_caller_max_tier=None)], query_f
        )
        assert feats[0]["graph_caller_tier"] == 0

    def test_graph_is_implementor(self):
        query_f = {"query_len": 10, "has_identifier": False, "has_path": False,
                    "identifier_density": 0.0, "term_count": 1}
        feats = extract_ranker_features(
            [self._make_candidate(graph_edge_type="implementor")], query_f
        )
        assert feats[0]["graph_is_implementor"] is True
        assert feats[0]["graph_is_caller"] is False

    def test_graph_is_doc_xref(self):
        query_f = {"query_len": 10, "has_identifier": False, "has_path": False,
                    "identifier_density": 0.0, "term_count": 1}
        feats = extract_ranker_features(
            [self._make_candidate(graph_edge_type="doc_xref")], query_f
        )
        assert feats[0]["graph_is_doc_xref"] is True

    def test_is_endpoint(self):
        query_f = {"query_len": 10, "has_identifier": False, "has_path": False,
                    "identifier_density": 0.0, "term_count": 1}
        feats = extract_ranker_features([self._make_candidate()], query_f)
        assert feats[0]["is_endpoint"] is True

    def test_test_coverage_count(self):
        query_f = {"query_len": 10, "has_identifier": False, "has_path": False,
                    "identifier_density": 0.0, "term_count": 1}
        feats = extract_ranker_features([self._make_candidate()], query_f)
        assert feats[0]["test_coverage_count"] == 3

    def test_lex_hit_count(self):
        query_f = {"query_len": 10, "has_identifier": False, "has_path": False,
                    "identifier_density": 0.0, "term_count": 1}
        feats = extract_ranker_features([self._make_candidate()], query_f)
        assert feats[0]["lex_hit_count"] == 2

    def test_locality_features(self):
        query_f = {"query_len": 10, "has_identifier": False, "has_path": False,
                    "identifier_density": 0.0, "term_count": 1}
        feats = extract_ranker_features([self._make_candidate()], query_f)
        assert feats[0]["seed_path_distance"] == 1
        assert feats[0]["same_package"] is True
        assert feats[0]["package_distance"] == 0


# ===================================================================
# Cutoff features
# ===================================================================


class TestCutoffEntropy:
    """Verify cutoff now includes score_entropy and cumulative_mass_top10."""

    def test_entropy_present(self):
        candidates = [{"ranker_score": 0.9 - i * 0.1, "retriever_hits": 1} for i in range(5)]
        query_f = {"query_len": 10, "has_identifier": False, "has_path": False}
        repo_f = {"object_count": 100, "file_count": 20}
        feats = extract_cutoff_features(candidates, query_f, repo_f)
        assert "score_entropy" in feats
        assert feats["score_entropy"] > 0.0

    def test_cumulative_mass_present(self):
        candidates = [{"ranker_score": 1.0 - i * 0.05, "retriever_hits": 2} for i in range(15)]
        query_f = {"query_len": 10, "has_identifier": False, "has_path": False}
        repo_f = {"object_count": 100, "file_count": 20}
        feats = extract_cutoff_features(candidates, query_f, repo_f)
        assert "cumulative_mass_top10" in feats
        assert 0.0 < feats["cumulative_mass_top10"] <= 1.0

    def test_empty_candidates(self):
        feats = extract_cutoff_features(
            [], {"query_len": 5, "has_identifier": False, "has_path": False},
            {"object_count": 0, "file_count": 0},
        )
        assert feats["score_entropy"] == 0.0
        assert feats["cumulative_mass_top10"] == 0.0
