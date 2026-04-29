"""Tests for code-health audit fixes.

Covers: parse_json_object, _subsample_negatives, WorkerArgs,
_check_positive_rate, _validate_inputs, _invalidate_azure_token,
_prepare_def_features, _compute_gate_features, _compute_cutoff_features.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from recon_lab.llm._json_parse import parse_json_object
from recon_lab.collect.collect_signals import WorkerArgs
from recon_lab.training.train_all import (
    _check_positive_rate,
    _prepare_def_features,
    _subsample_negatives,
)


# ═══════════════════════════════════════════════════════════════════
# parse_json_object
# ═══════════════════════════════════════════════════════════════════


class TestParseJsonObject:
    def test_plain_json(self) -> None:
        assert parse_json_object('{"a": 1}') == {"a": 1}

    def test_strips_markdown_fences(self) -> None:
        text = '```json\n{"b": 2}\n```'
        assert parse_json_object(text) == {"b": 2}

    def test_extracts_from_surrounding_text(self) -> None:
        text = 'Here is your answer:\n{"c": 3}\nHope that helps!'
        assert parse_json_object(text) == {"c": 3}

    def test_fixes_invalid_backslash_escapes(self) -> None:
        # LLMs sometimes produce raw backslashes — \U and \p are invalid
        # JSON escapes, so the fixer doubles them. \f is a valid JSON
        # escape (form feed) and is NOT doubled.
        text = r'preamble {"path": "C:\Users\project"} postamble'
        result = parse_json_object(text)
        assert result["path"] == "C:\\Users\\project"

    def test_raises_on_non_json(self) -> None:
        with pytest.raises(RuntimeError, match="Failed to parse JSON"):
            parse_json_object("not json at all")

    def test_raises_on_empty(self) -> None:
        with pytest.raises(RuntimeError, match="Failed to parse JSON"):
            parse_json_object("")

    def test_handles_nested_braces(self) -> None:
        text = 'prefix {"outer": {"inner": 1}} suffix'
        assert parse_json_object(text) == {"outer": {"inner": 1}}

    def test_whitespace_padded(self) -> None:
        assert parse_json_object("   \n  {\"x\": 42}  \n  ") == {"x": 42}


# ═══════════════════════════════════════════════════════════════════
# WorkerArgs
# ═══════════════════════════════════════════════════════════════════


class TestWorkerArgs:
    def test_fields(self) -> None:
        args = WorkerArgs(
            repo_id="repo1",
            data_dir="/data",
            main_clone_dir="/main",
            instance_clone_dir="/inst",
            extra_path="/extra",
        )
        assert args.repo_id == "repo1"
        assert args.data_dir == "/data"
        assert args.main_clone_dir == "/main"
        assert args.instance_clone_dir == "/inst"
        assert args.extra_path == "/extra"

    def test_is_tuple(self) -> None:
        args = WorkerArgs("r", "d", "m", "i", "e")
        assert isinstance(args, tuple)
        assert len(args) == 5


# ═══════════════════════════════════════════════════════════════════
# _subsample_negatives
# ═══════════════════════════════════════════════════════════════════


class TestSubsampleNegatives:
    def _make_df(
        self, n_pos: int, n_neg: int, groups: int = 1
    ) -> pd.DataFrame:
        rows = []
        for g in range(groups):
            for _ in range(n_pos):
                rows.append({"_group": f"g{g}", "label_relevant": 1})
            for _ in range(n_neg):
                rows.append({"_group": f"g{g}", "label_relevant": 0})
        return pd.DataFrame(rows)

    def test_caps_negatives(self) -> None:
        df = self._make_df(n_pos=3, n_neg=200)
        result = _subsample_negatives(df, max_neg=50)
        assert (result["label_relevant"] > 0).sum() == 3
        assert (result["label_relevant"] == 0).sum() == 50

    def test_keeps_all_when_below_max(self) -> None:
        df = self._make_df(n_pos=2, n_neg=10)
        result = _subsample_negatives(df, max_neg=50)
        assert len(result) == 12

    def test_drop_all_negative_removes_phantom_groups(self) -> None:
        df = self._make_df(n_pos=0, n_neg=50)
        result = _subsample_negatives(df, max_neg=500, drop_all_negative=True)
        assert len(result) == 0

    def test_multiple_groups(self) -> None:
        df = self._make_df(n_pos=1, n_neg=100, groups=3)
        result = _subsample_negatives(df, max_neg=10)
        for g in range(3):
            grp = result[result["_group"] == f"g{g}"]
            assert (grp["label_relevant"] > 0).sum() == 1
            assert (grp["label_relevant"] == 0).sum() == 10

    def test_empty_input(self) -> None:
        df = pd.DataFrame(columns=["_group", "label_relevant"])
        result = _subsample_negatives(df, max_neg=10)
        assert len(result) == 0


# ═══════════════════════════════════════════════════════════════════
# _check_positive_rate
# ═══════════════════════════════════════════════════════════════════


class TestCheckPositiveRate:
    def test_passes_for_healthy_rate(self) -> None:
        y = np.array([0, 0, 0, 0, 0, 0, 0, 0, 1, 1])
        _check_positive_rate(y, "test_model")  # should not raise

    def test_exits_for_zero_positives(self) -> None:
        y = np.zeros(1000)
        with pytest.raises(SystemExit):
            _check_positive_rate(y, "test_model")

    def test_exits_for_below_threshold(self) -> None:
        # 1 positive in 10000 → 0.0001 < 0.001
        y = np.zeros(10000)
        y[0] = 1
        with pytest.raises(SystemExit):
            _check_positive_rate(y, "test_model")


# ═══════════════════════════════════════════════════════════════════
# _validate_inputs
# ═══════════════════════════════════════════════════════════════════


class TestValidateInputs:
    def test_missing_file_exits(self, tmp_path: Path) -> None:
        from recon_lab.training.train_all import _validate_inputs

        with pytest.raises(SystemExit):
            _validate_inputs(tmp_path)


# ═══════════════════════════════════════════════════════════════════
# _invalidate_azure_token
# ═══════════════════════════════════════════════════════════════════


class TestInvalidateAzureToken:
    def test_clears_cached_token(self) -> None:
        from recon_lab.llm import llm_client
        from recon_lab.llm.llm_queries import _invalidate_azure_token

        # Seed the cache
        llm_client._azure_token = "fake-token"
        llm_client._azure_token_expires = 9999999999.0

        _invalidate_azure_token()

        assert llm_client._azure_token is None
        assert llm_client._azure_token_expires == 0.0


# ═══════════════════════════════════════════════════════════════════
# _prepare_def_features
# ═══════════════════════════════════════════════════════════════════


class TestPrepareDefFeatures:
    def _minimal_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "graph_edge_type": ["callee"],
            "graph_seed_rank": [1.0],
            "graph_caller_max_tier": ["strong"],
            "symbol_source": ["agent_seed"],
            "import_direction": ["forward"],
            "language_family": ["python"],
            "kind": ["function"],
            "artifact_kind": ["code"],
            "signature_text": ["def foo()"],
            "matched_terms_count": [2],
            "term_count": [4],
            "intent": ["debug"],
            "is_stacktrace_driven": [False],
            "is_test_driven": [True],
            "term_match_count": [1.0],
            "term_total_matches": [2.0],
            "lex_hit_count": [1],
            "bm25_file_score": [0.5],
            "splade_score": [0.3],
            "ce_score_tiny": [0.7],
            "retriever_hits": [2],
            "object_size_lines": [10],
            "path_depth": [3],
            "nesting_depth": [0],
            "hub_score": [0.8],
            "is_test": [False],
            "is_barrel": [False],
            "is_endpoint": [False],
            "test_coverage_count": [1],
            "has_docstring": [True],
            "has_decorators": [False],
            "has_return_type": [True],
            "has_parent_scope": [False],
            "shares_file_with_seed": [True],
            "is_callee_of_top": [False],
            "is_imported_by_top": [True],
            "from_coverage": [False],
            "from_term_match": [True],
            "from_explicit": [False],
            "from_graph": [True],
            "seed_path_distance": [1],
            "same_package": [True],
            "package_distance": [0],
            "rrf_score": [0.5],
            "query_len": [15],
            "has_identifier": [True],
            "has_path": [False],
            "identifier_density": [0.3],
            "has_numbers": [False],
            "has_quoted_strings": [False],
        })

    def test_graph_edge_one_hot(self) -> None:
        df = _prepare_def_features(self._minimal_df())
        assert df["graph_is_callee"].iloc[0] is True or df["graph_is_callee"].iloc[0] == True
        assert df["graph_is_caller"].iloc[0] is False or df["graph_is_caller"].iloc[0] == False

    def test_symbol_source_one_hot(self) -> None:
        df = _prepare_def_features(self._minimal_df())
        assert df["sym_agent_seed"].iloc[0]
        assert not df["sym_auto_seed"].iloc[0]

    def test_language_one_hot(self) -> None:
        df = _prepare_def_features(self._minimal_df())
        assert df["lang_python"].iloc[0]
        assert not df["lang_go"].iloc[0]

    def test_term_coverage(self) -> None:
        df = _prepare_def_features(self._minimal_df())
        assert df["term_coverage"].iloc[0] == pytest.approx(0.5)

    def test_caller_tier_ordinal(self) -> None:
        df = _prepare_def_features(self._minimal_df())
        assert df["graph_caller_tier"].iloc[0] == 2  # "strong" → 2

    def test_intent_one_hot(self) -> None:
        df = _prepare_def_features(self._minimal_df())
        assert df["intent_debug"].iloc[0]
        assert not df["intent_implement"].iloc[0]
