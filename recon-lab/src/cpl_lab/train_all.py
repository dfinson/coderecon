"""Train 4 structural models.

Models:
  1. Gate (structural)        — query routing
  2. File Ranker (structural) — file-level LambdaMART
  3. Def Ranker (structural)  — def-level LambdaMART
  4. Cutoff (structural)      — N* regressor from structural ranker scores

Usage::

    python -m cpl_lab.train_all --data-dir ~/.recon/recon-lab/data --output-dir output/
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from cpl_lab.schema import OK_QUERY_TYPES

# ═══════════════════════════════════════════════════════════════════
# Feature definitions
# ═══════════════════════════════════════════════════════════════════

# -- Def Ranker features ------------------------------------------------
DEF_RANKER_FEATURES = [
    "term_match_count", "term_total_matches", "lex_hit_count", "bm25_file_score",
    "graph_is_callee", "graph_is_caller", "graph_is_sibling",
    "graph_is_implementor", "graph_is_doc_xref",
    "graph_seed_rank", "graph_caller_tier",
    "sym_agent_seed", "sym_auto_seed", "sym_task_extracted", "sym_path_mention",
    "import_forward", "import_reverse", "import_barrel", "import_test_pair",
    "retriever_hits",
    "object_size_lines", "path_depth", "nesting_depth",
    "hub_score", "is_test", "is_endpoint", "test_coverage_count",
    "has_docstring", "has_decorators", "has_return_type", "has_parent_scope",
    "has_signature",
    "from_coverage",
    "seed_path_distance", "same_package", "package_distance",
    "rrf_score",
    "query_len", "has_identifier", "has_path", "term_count",
]

# -- File Ranker features ------------------------------------------------
# Aggregated from def-level signals per (query, file) group.
FILE_RANKER_FEATURES = [
    # Term match
    "max_term_match", "sum_term_matches", "max_lex_hits", "max_bm25_file_score",
    # Graph
    "any_callee", "any_caller", "any_sibling", "any_doc_xref", "any_implementor",
    "best_graph_seed_rank", "best_caller_tier",
    # Symbol
    "any_agent_seed", "any_auto_seed", "any_task_extracted", "any_path_mention",
    # Import
    "any_import_forward", "any_import_reverse", "any_import_barrel", "any_import_test_pair",
    # Retriever agreement
    "max_retriever_hits", "sum_retriever_hits",
    # Coverage
    "any_from_coverage",
    # RRF
    "max_rrf_score",
    # File-level metadata
    "num_defs_in_file", "mean_hub_score", "max_hub_score",
    "is_test", "path_depth",
    "any_docstring", "any_decorators", "any_return_type",
    # Locality
    "min_seed_path_distance", "same_package",
    # Query features
    "query_len", "has_identifier", "has_path", "term_count",
]

# -- Gate features -------------------------------------------------------
GATE_FEATURES = [
    "query_len", "identifier_density", "has_path",
    "has_numbers", "has_quoted_strings",
    "has_agent_seeds", "agent_seed_count",
    "object_count", "file_count",
    "total_candidates",
    "top_score", "score_p25", "score_p50", "score_p75",
    "path_entropy", "cluster_count",
    "agreement_mean", "agreement_std",
]

GATE_LABELS = {"OK": 0, "UNSAT": 1, "BROAD": 2, "AMBIG": 3}

# -- Cutoff features ------------------------------------------------------
CUTOFF_FEATURES = [
    "query_len", "has_identifier", "has_path", "total_candidates",
    "top_score", "score_p25", "score_p50", "score_p75",
    "max_gap", "max_gap_pos", "score_var",
    "score_entropy", "cumulative_mass_top10",
    "agreement_mean",
]

# ═══════════════════════════════════════════════════════════════════
# Load columns from parquet
# ═══════════════════════════════════════════════════════════════════

_LOAD_COLS = [
    "run_id", "query_id", "query_type", "repo_set", "label_relevant", "label_gate",
    "path", "kind", "name", "start_line", "end_line",
    "term_match_count", "term_total_matches", "lex_hit_count", "bm25_file_score",
    "graph_edge_type", "graph_seed_rank", "graph_caller_max_tier",
    "symbol_source", "import_direction", "from_coverage", "retriever_hits",
    "object_size_lines", "path_depth", "nesting_depth",
    "hub_score", "is_test", "is_endpoint", "test_coverage_count",
    "has_docstring", "has_decorators", "has_return_type", "has_parent_scope",
    "seed_path_distance", "same_package", "package_distance",
    "rrf_score",
    "signature_text", "parent_dir",
    "query_len", "has_identifier", "has_path", "identifier_density",
    "has_numbers", "has_quoted_strings", "term_count",
    "object_count", "file_count",
]


# ═══════════════════════════════════════════════════════════════════
# Feature preparation
# ═══════════════════════════════════════════════════════════════════

def _prepare_def_features(df: pd.DataFrame) -> pd.DataFrame:
    """Encode categorical signals into binary columns."""
    df = df.copy()

    # Graph edge type → binary
    df["graph_is_callee"] = df["graph_edge_type"] == "callee"
    df["graph_is_caller"] = df["graph_edge_type"] == "caller"
    df["graph_is_sibling"] = df["graph_edge_type"] == "sibling"
    df["graph_is_implementor"] = df["graph_edge_type"] == "implementor"
    df["graph_is_doc_xref"] = df["graph_edge_type"] == "doc_xref"

    # Graph caller tier → ordinal
    _tier_map = {"proven": 3, "strong": 2, "anchored": 1, "unknown": 0}
    df["graph_caller_tier"] = (
        df.get("graph_caller_max_tier", pd.Series(dtype=object))
        .map(_tier_map).fillna(0).astype(int)
    )

    # Symbol source → binary
    df["sym_agent_seed"] = df["symbol_source"] == "agent_seed"
    df["sym_auto_seed"] = df["symbol_source"] == "auto_seed"
    df["sym_task_extracted"] = df["symbol_source"] == "task_extracted"
    df["sym_path_mention"] = df["symbol_source"] == "path_mention"

    # Import direction → binary
    df["import_forward"] = df["import_direction"] == "forward"
    df["import_reverse"] = df["import_direction"] == "reverse"
    df["import_barrel"] = df["import_direction"] == "barrel"
    df["import_test_pair"] = df["import_direction"] == "test_pair"

    # Has signature
    df["has_signature"] = df.get("signature_text", pd.Series(dtype=object)).notna()

    # Fill NaN for numeric columns
    for col in DEF_RANKER_FEATURES:
        if col in df.columns:
            df[col] = df[col].fillna(0)
        else:
            df[col] = 0

    if "term_count" not in df.columns:
        df["term_count"] = 0

    return df


def _aggregate_file_features(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate def-level features to file level within each query group.

    Groups by (run_id, query_id, path) and computes file-level aggregates.
    """
    group_key = ["run_id", "query_id", "path"]

    agg = df.groupby(group_key, sort=False).agg(
        # Term match
        max_term_match=("term_match_count", "max"),
        sum_term_matches=("term_total_matches", "sum"),
        max_lex_hits=("lex_hit_count", "max"),
        max_bm25_file_score=("bm25_file_score", "max"),
        # Graph
        any_callee=("graph_is_callee", "any"),
        any_caller=("graph_is_caller", "any"),
        any_sibling=("graph_is_sibling", "any"),
        any_doc_xref=("graph_is_doc_xref", "any"),
        any_implementor=("graph_is_implementor", "any"),
        best_graph_seed_rank=("graph_seed_rank", "min"),
        best_caller_tier=("graph_caller_tier", "max"),
        # Symbol
        any_agent_seed=("sym_agent_seed", "any"),
        any_auto_seed=("sym_auto_seed", "any"),
        any_task_extracted=("sym_task_extracted", "any"),
        any_path_mention=("sym_path_mention", "any"),
        # Import
        any_import_forward=("import_forward", "any"),
        any_import_reverse=("import_reverse", "any"),
        any_import_barrel=("import_barrel", "any"),
        any_import_test_pair=("import_test_pair", "any"),
        # Retriever agreement
        max_retriever_hits=("retriever_hits", "max"),
        sum_retriever_hits=("retriever_hits", "sum"),
        # Coverage
        any_from_coverage=("from_coverage", "any"),
        # RRF
        max_rrf_score=("rrf_score", "max"),
        # File metadata
        num_defs_in_file=("path", "count"),
        mean_hub_score=("hub_score", "mean"),
        max_hub_score=("hub_score", "max"),
        is_test=("is_test", "any"),
        any_docstring=("has_docstring", "any"),
        any_decorators=("has_decorators", "any"),
        any_return_type=("has_return_type", "any"),
        # Locality
        min_seed_path_distance=("seed_path_distance", "min"),
        same_package=("same_package", "any"),
        # Label: file is relevant if ANY def in it is GT
        label_relevant=("label_relevant", "max"),
    ).reset_index()

    # Copy path_depth (same for all defs in same file)
    pd_col = df.groupby(group_key, sort=False)["path_depth"].first().reset_index()
    agg = agg.merge(pd_col, on=group_key, how="left")

    # Copy query features (same for all candidates in a query group)
    for qf in ["query_len", "has_identifier", "has_path", "term_count"]:
        qf_col = df.groupby(group_key, sort=False)[qf].first().reset_index()
        agg = agg.merge(qf_col, on=group_key, how="left")

    # Fill NaN
    for col in FILE_RANKER_FEATURES:
        if col in agg.columns:
            agg[col] = agg[col].fillna(0)
        else:
            agg[col] = 0

    return agg


def _subsample_negatives(
    df: pd.DataFrame,
    max_neg: int = 500,
    group_col_name: str = "_group",
    rng: np.random.RandomState | None = None,
    drop_all_negative: bool = False,
) -> pd.DataFrame:
    """Keep all positives, sample at most *max_neg* negatives per group.

    When *drop_all_negative* is True, groups with zero positives are
    discarded entirely.  This removes "phantom GT" queries where all
    ground-truth defs are newly-created code that doesn't exist at HEAD.
    """
    if rng is None:
        rng = np.random.RandomState(42)
    parts: list[pd.DataFrame] = []
    dropped = 0
    for _, grp in df.groupby(group_col_name, sort=False):
        pos = grp[grp["label_relevant"] > 0]
        neg = grp[grp["label_relevant"] == 0]
        if drop_all_negative and len(pos) == 0:
            dropped += 1
            continue
        if len(neg) > max_neg:
            neg = neg.sample(n=max_neg, random_state=rng)
        parts.append(pd.concat([pos, neg]))
    if dropped:
        print(f"    Dropped {dropped} all-negative groups (phantom GT)")
    if not parts:
        return df.iloc[:0]
    return pd.concat(parts, ignore_index=True)


# ═══════════════════════════════════════════════════════════════════
# Streaming data loading
# ═══════════════════════════════════════════════════════════════════

def _available_columns(pq_path: Path) -> set[str]:
    """Get available column names from the parquet file."""
    pf = pq.ParquetFile(pq_path)
    return set(pf.schema_arrow.names)


def _stream_def_data(
    merged_dir: Path,
    repo_sets: set[str],
    ok_only: bool = True,
    max_neg: int = 100,
    drop_all_negative: bool = True,
) -> pd.DataFrame:
    """Stream parquet row groups, filter, prepare features, subsample.

    By default drops query groups with zero positive candidates
    (phantom GT — defs that would be created by the patch but don't
    exist in the repo at HEAD).
    """
    pq_path = merged_dir / "candidates_rank.parquet"
    available = _available_columns(pq_path)
    load_cols = [c for c in _LOAD_COLS if c in available]
    pf = pq.ParquetFile(pq_path)

    rng = np.random.RandomState(42)
    parts: list[pd.DataFrame] = []

    for rg_idx in range(pf.metadata.num_row_groups):
        table = pf.read_row_group(rg_idx, columns=load_cols)
        chunk = table.to_pandas()
        del table

        chunk = chunk[chunk["repo_set"].isin(repo_sets)]
        if chunk.empty:
            continue

        if ok_only:
            chunk = chunk[chunk["query_type"].isin(OK_QUERY_TYPES)]
            if chunk.empty:
                continue

        chunk = _prepare_def_features(chunk)
        chunk["_group"] = chunk["run_id"].astype(str) + "__" + chunk["query_id"].astype(str)
        chunk = _subsample_negatives(chunk, max_neg=max_neg, rng=rng,
                                     drop_all_negative=drop_all_negative)
        parts.append(chunk)

    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


# ═══════════════════════════════════════════════════════════════════
# Gate feature extraction
# ═══════════════════════════════════════════════════════════════════

def _compute_gate_features(group: pd.DataFrame) -> dict:
    """Compute gate features from a per-query candidate group."""
    first = group.iloc[0]
    f: dict = {}

    # Query features
    f["query_len"] = first.get("query_len", 0)
    f["identifier_density"] = first.get("identifier_density", 0.0)
    f["has_path"] = first.get("has_path", False)
    f["has_numbers"] = first.get("has_numbers", False)
    f["has_quoted_strings"] = first.get("has_quoted_strings", False)

    # Seed presence
    f["has_agent_seeds"] = (group["symbol_source"] == "agent_seed").any()
    f["agent_seed_count"] = (group["symbol_source"] == "agent_seed").sum()

    # Repo features
    f["object_count"] = first.get("object_count", 0)
    f["file_count"] = first.get("file_count", 0)

    # Candidate pool
    f["total_candidates"] = len(group)

    # Use retriever agreement as score proxy for distribution features
    pool_scores = group["retriever_hits"].fillna(0).sort_values(ascending=False).values
    n = len(pool_scores)

    if n == 0:
        for k in ["top_score", "score_p25", "score_p50", "score_p75"]:
            f[k] = 0.0
        f["path_entropy"] = 0.0
        f["cluster_count"] = 0
        f["agreement_mean"] = 0.0
        f["agreement_std"] = 0.0
    else:
        f["top_score"] = float(pool_scores[0])
        f["score_p25"] = float(pool_scores[min(int(n * 0.25), n - 1)])
        f["score_p50"] = float(pool_scores[min(int(n * 0.5), n - 1)])
        f["score_p75"] = float(pool_scores[min(int(n * 0.75), n - 1)])

        # Path entropy
        dirs = group["parent_dir"].dropna().values
        if len(dirs) > 0:
            dir_counts = Counter(dirs)
            total = sum(dir_counts.values())
            probs = [c / total for c in dir_counts.values()]
            f["path_entropy"] = -sum(p * math.log(p) for p in probs if p > 0)
        else:
            f["path_entropy"] = 0.0

        # Cluster count
        paths = group["path"].dropna().values
        depth2 = set()
        for p in paths:
            parts = str(p).split("/")
            if len(parts) >= 2:
                depth2.add("/".join(parts[:2]))
        f["cluster_count"] = len(depth2)

        # Agreement
        hits = group["retriever_hits"].fillna(0).values
        f["agreement_mean"] = float(hits.mean())
        f["agreement_std"] = float(hits.std()) if len(hits) > 1 else 0.0

    # Label
    f["label_gate"] = first.get("label_gate", "OK")

    return f


# ═══════════════════════════════════════════════════════════════════
# Cutoff helpers
# ═══════════════════════════════════════════════════════════════════

def _compute_n_star(scores: np.ndarray, labels: np.ndarray, max_n: int = 100) -> int:
    """Find N that maximizes F1 between top-N and ground truth."""
    n_relevant = labels.sum()
    if n_relevant == 0:
        return 0
    best_f1, best_n = 0.0, 1
    tp_cumsum = np.cumsum(labels)
    for n in range(1, min(len(scores), max_n) + 1):
        tp = tp_cumsum[n - 1]
        prec = tp / n
        rec = tp / n_relevant
        if prec + rec > 0:
            f1 = 2 * prec * rec / (prec + rec)
            if f1 > best_f1:
                best_f1 = f1
                best_n = n
    return best_n


def _cutoff_features_from_scores(
    scores: np.ndarray, retriever_hits: np.ndarray,
    query_len: int, has_identifier: bool, has_path: bool, n_candidates: int,
) -> dict:
    """Compute query-level cutoff features from ranked score list."""
    n = len(scores)
    f: dict = {}
    f["query_len"] = query_len
    f["has_identifier"] = has_identifier
    f["has_path"] = has_path
    f["total_candidates"] = n_candidates

    if n == 0:
        for k in ["top_score", "score_p25", "score_p50", "score_p75",
                   "max_gap", "max_gap_pos", "score_var",
                   "score_entropy", "cumulative_mass_top10", "agreement_mean"]:
            f[k] = 0.0
        return f

    f["top_score"] = float(scores[0])
    f["score_p25"] = float(np.percentile(scores, 75))
    f["score_p50"] = float(np.percentile(scores, 50))
    f["score_p75"] = float(np.percentile(scores, 25))

    gaps = np.diff(scores) if n > 1 else np.array([0.0])
    f["max_gap"] = float(np.abs(gaps).max()) if len(gaps) > 0 else 0.0
    f["max_gap_pos"] = float((np.abs(gaps).argmax() + 1) / n) if len(gaps) > 0 else 0.0
    f["score_var"] = float(scores.var())

    total = float(scores.sum()) if scores.sum() > 0 else 1.0
    probs = scores[scores > 0] / total
    f["score_entropy"] = float(-np.sum(probs * np.log(probs))) if len(probs) > 0 else 0.0
    f["cumulative_mass_top10"] = float(scores[:10].sum()) / total if total > 0 else 0.0

    f["agreement_mean"] = float(retriever_hits.mean())
    return f


# ═══════════════════════════════════════════════════════════════════
# Training functions
# ═══════════════════════════════════════════════════════════════════

def _train_lgb_ranker(
    X: np.ndarray, y: np.ndarray, groups: np.ndarray,
    feature_names: list[str], num_boost_round: int = 500,
    params_override: dict | None = None,
) -> lgb.Booster:
    """Train a LambdaMART ranker."""
    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [5, 10, 20],
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 10,
        "verbose": -1,
    }
    if params_override:
        params.update(params_override)
    ds = lgb.Dataset(X, label=y, group=groups, feature_name=feature_names)
    return lgb.train(params, ds, num_boost_round=num_boost_round)


def _train_lgb_classifier(
    X: np.ndarray, y: np.ndarray,
    feature_names: list[str], num_class: int = 4,
    num_boost_round: int = 300,
) -> lgb.Booster:
    """Train a multiclass classifier."""
    params = {
        "objective": "multiclass",
        "num_class": num_class,
        "metric": "multi_logloss",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "verbose": -1,
    }
    ds = lgb.Dataset(X, label=y, feature_name=feature_names)
    return lgb.train(params, ds, num_boost_round=num_boost_round)


def _train_lgb_regressor(
    X: np.ndarray, y: np.ndarray,
    feature_names: list[str], num_boost_round: int = 300,
) -> lgb.Booster:
    """Train a regression model."""
    params = {
        "objective": "regression",
        "metric": "rmse",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "verbose": -1,
    }
    ds = lgb.Dataset(X, label=y, feature_name=feature_names)
    return lgb.train(params, ds, num_boost_round=num_boost_round)


def _save_model(booster: lgb.Booster, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(path))


# ═══════════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════════

def train_all(data_dir: Path, output_dir: Path, skip_merge: bool = False) -> dict:
    """Run the full 8-model training pipeline."""
    merged_dir = data_dir / "merged"

    # Validate
    for req in ("candidates_rank.parquet",):
        if not (merged_dir / req).exists():
            print(f"Missing {merged_dir / req}", file=sys.stderr)
            sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {}

    # ── 0. Optionally re-merge signals ────────────────────────────
    if not skip_merge:
        from cpl_lab.merge_signals import merge_signals
        print("=== Merging Signals ===")
        sig_summary = merge_signals(data_dir)
        print(f"  {sig_summary['total_candidates']} candidates, "
              f"positive rate: {sig_summary['positive_rate']:.3f}")

    # ── 1. Load def-level data for ranker training ────────────────
    # Use ranker-gate if available, fall back to all sets
    print("\n=== Loading Ranker Data ===")
    rg_df = _stream_def_data(merged_dir, repo_sets={"ranker-gate"}, ok_only=True, max_neg=50)
    if rg_df.empty:
        print("  No ranker-gate data, using all available sets")
        rg_df = _stream_def_data(merged_dir, repo_sets={"ranker-gate", "cutoff", "eval"}, ok_only=True, max_neg=50)
    if rg_df.empty:
        print("  ERROR: No training data found!", file=sys.stderr)
        sys.exit(1)
    print(f"  {len(rg_df)} candidates, "
          f"{rg_df['_group'].nunique()} query groups")

    # ── 2. Train Def Ranker ────────────────────────────────────────────
    name = "def_ranker"
    features = DEF_RANKER_FEATURES
    print(f"\n=== Training Def Ranker ===")

    df = rg_df.sort_values(["run_id", "query_id"]).reset_index(drop=True)
    group_col = df["run_id"].astype(str) + "__" + df["query_id"].astype(str)
    group_sizes = df.groupby(group_col, sort=True).size().values

    X = df[features].fillna(0).values
    y = df["label_relevant"].astype(int).values

    booster = _train_lgb_ranker(X, y, group_sizes, features)
    _save_model(booster, output_dir / f"{name}.lgbm")

    summary[name] = {
        "candidates": len(df),
        "groups": len(group_sizes),
        "positive_rate": float(y.mean()),
        "features": len(features),
    }
    print(f"  {summary[name]}")

    # ── 3. Train File Rankers ─────────────────────────────────────
    import gc
    print("\n=== Building File-Level Data ===")
    file_df = _aggregate_file_features(rg_df)
    del rg_df; gc.collect()
    file_df["_group"] = file_df["run_id"].astype(str) + "__" + file_df["query_id"].astype(str)

    # Binarize label for file ranker (any GT def in file → 1)
    file_df["label_relevant"] = (file_df["label_relevant"] > 0).astype(int)

    # Subsample negatives at file level
    file_df = _subsample_negatives(file_df, max_neg=50)
    print(f"  {len(file_df)} file rows, {file_df['_group'].nunique()} groups")

    for variant, features in [("structural", FILE_RANKER_FEATURES)]:
        name = f"file_ranker_{variant}"
        print(f"\n=== Training File Ranker ({variant}) ===")

        df = file_df.sort_values(["run_id", "query_id"]).reset_index(drop=True)
        group_col = df["run_id"].astype(str) + "__" + df["query_id"].astype(str)
        group_sizes = df.groupby(group_col, sort=True).size().values

        X = df[features].fillna(0).values
        y = df["label_relevant"].astype(int).values

        booster = _train_lgb_ranker(X, y, group_sizes, features)
        _save_model(booster, output_dir / f"{name}.lgbm")

        summary[name] = {
            "files": len(df),
            "groups": len(group_sizes),
            "positive_rate": float(y.mean()),
            "features": len(features),
        }
        print(f"  {summary[name]}")

    # ── 4. Train Gate ─────────────────────────────────────────────
    del file_df; gc.collect()
    print("\n=== Loading Gate Data ===")
    all_df = _stream_def_data(merged_dir, repo_sets={"ranker-gate", "cutoff", "eval"}, ok_only=False, max_neg=50)
    print(f"  {len(all_df)} candidates for gate extraction")

    gate_rows: list[dict] = []
    for qid, grp in all_df.groupby("query_id", sort=False):
        gate_rows.append(_compute_gate_features(grp))

    gate_df = pd.DataFrame(gate_rows)
    print(f"  {len(gate_df)} gate queries")

    for variant, features in [("structural", GATE_FEATURES)]:
        name = f"gate_{variant}"
        print(f"\n=== Training Gate ({variant}) ===")

        X = gate_df[features].fillna(0).values
        y = gate_df["label_gate"].map(GATE_LABELS).values

        booster = _train_lgb_classifier(X, y, features)
        _save_model(booster, output_dir / f"{name}.lgbm")

        label_counts = Counter(gate_df["label_gate"].values)
        summary[name] = {
            "queries": len(gate_df),
            "label_distribution": dict(label_counts),
            "features": len(features),
        }
        print(f"  {summary[name]}")

    # ── 5. Train Cutoff (depends on def ranker) ───────────────────
    del all_df, gate_df; gc.collect()
    print("\n=== Loading Cutoff Data ===")
    cutoff_src = _stream_def_data(merged_dir, repo_sets={"cutoff", "ranker-gate", "eval"}, ok_only=True, max_neg=50)
    print(f"  {len(cutoff_src)} cutoff candidates")

    for variant, ranker_features in [("structural", DEF_RANKER_FEATURES)]:
        name = f"cutoff_{variant}"
        ranker_name = f"def_ranker_{variant}"
        ranker_path = output_dir / f"{ranker_name}.lgbm"
        print(f"\n=== Training Cutoff ({variant}) ===")

        ranker = lgb.Booster(model_file=str(ranker_path))

        # Score cutoff candidates with the trained ranker
        X_score = cutoff_src[ranker_features].fillna(0).values
        cutoff_src_copy = cutoff_src.copy()
        cutoff_src_copy["ranker_score"] = ranker.predict(X_score)

        # Compute N* per query
        cutoff_rows: list[dict] = []
        for (run_id, query_id), qdf in cutoff_src_copy.groupby(
            ["run_id", "query_id"], sort=False
        ):
            scores = qdf["ranker_score"].values
            labels = qdf["label_relevant"].astype(int).values
            order = np.argsort(-scores)

            n_star = _compute_n_star(scores[order], labels[order])
            feat = _cutoff_features_from_scores(
                scores[order],
                qdf["retriever_hits"].values[order],
                query_len=int(qdf["query_len"].iloc[0]),
                has_identifier=bool(qdf["has_identifier"].iloc[0]),
                has_path=bool(qdf["has_path"].iloc[0]),
                n_candidates=len(qdf),
            )
            feat["n_star"] = n_star
            cutoff_rows.append(feat)

        if not cutoff_rows:
            print(f"  [SKIP] No cutoff data for {variant}")
            continue

        cutoff_df = pd.DataFrame(cutoff_rows)
        X = cutoff_df[CUTOFF_FEATURES].fillna(0).values
        y = cutoff_df["n_star"].values

        booster = _train_lgb_regressor(X, y, CUTOFF_FEATURES)
        _save_model(booster, output_dir / f"{name}.lgbm")

        summary[name] = {
            "rows": len(cutoff_df),
            "n_star_mean": float(y.mean()),
            "n_star_std": float(y.std()),
            "features": len(CUTOFF_FEATURES),
        }
        print(f"  {summary[name]}")

    # ── Done ──────────────────────────────────────────────────────
    (output_dir / "training_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n=== All 4 models saved to {output_dir}/ ===")
    for name in sorted(summary):
        print(f"  {name}.lgbm")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train all 8 ranking models")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-merge", action="store_true")
    args = parser.parse_args()
    train_all(args.data_dir, args.output_dir, skip_merge=args.skip_merge)
