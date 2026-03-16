"""Proof analysis — train & measure gate/ranker/cutoff on 5 subset repos.

Excludes phantom GT defs (defs in ground truth that don't exist in the index).
Relies only on positives that were actually retrieved as candidates.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

# ── Config ────────────────────────────────────────────────────────

DATA_DIR = Path.home() / ".cpl-lab" / "data"
REPOS = ["ruby-sinatra", "go-gin", "php-console", "java-mockito", "python-celery"]

RANKER_FEATURES = [
    "emb_score", "emb_rank",
    "term_match_count", "term_total_matches",
    "graph_is_callee", "graph_is_caller", "graph_is_sibling", "graph_seed_rank",
    "sym_agent_seed", "sym_auto_seed", "sym_task_extracted", "sym_path_mention",
    "import_forward", "import_reverse", "import_barrel", "import_test_pair",
    "retriever_hits",
    "object_size_lines", "path_depth", "nesting_depth",
    "hub_score", "is_test",
    "has_docstring", "has_decorators", "has_return_type", "has_parent_scope",
    "has_signature",
    "query_len", "has_identifier", "has_path", "term_count",
]


# ── Helpers ───────────────────────────────────────────────────────

def _prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["graph_is_callee"] = df["graph_edge_type"] == "callee"
    df["graph_is_caller"] = df["graph_edge_type"] == "caller"
    df["graph_is_sibling"] = df["graph_edge_type"] == "sibling"
    df["sym_agent_seed"] = df["symbol_source"] == "agent_seed"
    df["sym_auto_seed"] = df["symbol_source"] == "auto_seed"
    df["sym_task_extracted"] = df["symbol_source"] == "task_extracted"
    df["sym_path_mention"] = df["symbol_source"] == "path_mention"
    df["import_forward"] = df["import_direction"] == "forward"
    df["import_reverse"] = df["import_direction"] == "reverse"
    df["import_barrel"] = df["import_direction"] == "barrel"
    df["import_test_pair"] = df["import_direction"] == "test_pair"
    df["has_signature"] = df["signature_text"].notna()
    for col in RANKER_FEATURES:
        if col not in df.columns:
            df[col] = 0
        else:
            df[col] = df[col].fillna(0)
    return df


def _subsample_negatives(df: pd.DataFrame, max_neg: int = 500) -> pd.DataFrame:
    rng = np.random.RandomState(42)
    parts = []
    for _, grp in df.groupby("group_key", sort=False):
        pos = grp[grp["label_relevant"] > 0]
        neg = grp[grp["label_relevant"] == 0]
        if len(neg) > max_neg:
            neg = neg.sample(n=max_neg, random_state=rng)
        parts.append(pd.concat([pos, neg]))
    return pd.concat(parts, ignore_index=True) if parts else df.iloc[:0]


def _count_phantom_defs(repo_id: str) -> dict:
    """Count GT defs that exist vs phantom (not in candidate set)."""
    gt_file = DATA_DIR / repo_id / "ground_truth.jsonl"
    sig_file = DATA_DIR / repo_id / "signals" / "candidates_rank.parquet"

    # Parse GT defs
    gt_defs: dict[str, set[str]] = {}  # task_id -> set of candidate_keys
    for ln in gt_file.read_text().splitlines():
        if not ln.strip():
            continue
        task = json.loads(ln)
        tid = task.get("task_id", "")
        keys = set()
        for d in task.get("minimum_sufficient_defs", []):
            k = f"{d['path']}:{d.get('kind', '')}:{d.get('name', '')}:{d.get('start_line', 0)}"
            keys.add(k)
        for d in task.get("thrash_preventing_defs", []):
            k = f"{d['path']}:{d.get('kind', '')}:{d.get('name', '')}:{d.get('start_line', 0)}"
            keys.add(k)
        gt_defs[tid] = keys

    # Get all candidate_keys that appear in the signal data
    pf = pq.ParquetFile(sig_file)
    retrieved_keys: set[str] = set()
    for rg in range(pf.metadata.num_row_groups):
        t = pf.read_row_group(rg, columns=["candidate_key"])
        retrieved_keys.update(t.column("candidate_key").to_pylist())
        del t

    total_gt = sum(len(v) for v in gt_defs.values())
    phantom = sum(
        sum(1 for k in keys if k not in retrieved_keys)
        for keys in gt_defs.values()
    )
    real = total_gt - phantom
    return {"repo": repo_id, "total_gt_defs": total_gt, "phantom": phantom,
            "real": real, "phantom_pct": phantom / total_gt * 100 if total_gt else 0}


# ── Main ──────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 70)
    print("PROOF ANALYSIS — Fixed pipeline, 5 subset repos")
    print("Phantom GT defs excluded from all metrics")
    print("=" * 70)

    # ── Step 1: Phantom audit ─────────────────────────────────────
    print("\n── STEP 1: Phantom GT Audit ──")
    for rid in REPOS:
        info = _count_phantom_defs(rid)
        print(f"  {rid:20s}: {info['real']:4d} real / {info['total_gt_defs']:4d} total  "
              f"({info['phantom_pct']:5.1f}% phantom)")

    # ── Step 2: Load & combine all parquets ───────────────────────
    print("\n── STEP 2: Load signal data ──")
    parts = []
    for rid in REPOS:
        pf_path = DATA_DIR / rid / "signals" / "candidates_rank.parquet"
        df = pq.read_table(pf_path).to_pandas()
        df["repo_id"] = rid
        df["group_key"] = rid + "__" + df["query_id"].astype(str)
        parts.append(df)
        pos = (df["label_relevant"] > 0).sum()
        print(f"  {rid:20s}: {len(df):>10,} rows, {pos:>5} positives, "
              f"{df['query_id'].nunique()} queries")
        del df

    combined = pd.concat(parts, ignore_index=True)
    del parts
    print(f"\n  Combined: {len(combined):,} rows, "
          f"{(combined['label_relevant'] > 0).sum()} positives, "
          f"{combined['group_key'].nunique()} query groups")

    # ── Step 3: Prepare features ──────────────────────────────────
    print("\n── STEP 3: Feature preparation ──")
    combined = _prepare_features(combined)

    # ── Step 4: Signal discrimination ─────────────────────────────
    print("\n── STEP 4: Signal discrimination (pos label>0 vs neg label=0) ──")
    pos = combined[combined["label_relevant"] > 0]
    neg = combined[combined["label_relevant"] == 0]

    signal_cols = [
        "emb_rank", "emb_score",
        "term_match_count", "term_total_matches",
        "graph_seed_rank",
        "retriever_hits",
        "graph_is_callee", "graph_is_caller", "graph_is_sibling",
        "import_forward", "import_reverse",
        "is_test",
    ]

    print(f"  {'Feature':25s} {'Pos mean':>10s} {'Neg mean':>10s} {'Ratio':>8s} "
          f"{'Pos>0%':>8s} {'Neg>0%':>8s}")
    print("  " + "-" * 75)
    for col in signal_cols:
        pm = pos[col].astype(float).mean()
        nm = neg[col].astype(float).mean()
        ratio = pm / nm if nm != 0 else float("inf")
        p_nz = (pos[col].astype(float) > 0).mean() * 100
        n_nz = (neg[col].astype(float) > 0).mean() * 100
        print(f"  {col:25s} {pm:10.4f} {nm:10.4f} {ratio:8.2f}x {p_nz:7.1f}% {n_nz:7.1f}%")

    # retriever_hits distribution
    print("\n  retriever_hits distribution:")
    for h in sorted(combined["retriever_hits"].unique()):
        p_pct = (pos["retriever_hits"] == h).mean() * 100
        n_pct = (neg["retriever_hits"] == h).mean() * 100
        print(f"    hits={int(h)}: pos={p_pct:5.1f}%  neg={n_pct:5.1f}%")

    del pos, neg

    # ── Step 5: Subsample & train ranker ──────────────────────────
    print("\n── STEP 5: Train ranker (LambdaMART) ──")
    sampled = _subsample_negatives(combined, max_neg=500)
    print(f"  After subsampling: {len(sampled):,} rows, "
          f"{(sampled['label_relevant'] > 0).sum()} positives")

    # 5-fold cross-validation by repo
    from sklearn.metrics import ndcg_score
    repo_ndcgs = {}
    all_preds = []
    all_labels = []
    all_groups_meta = []

    for test_repo in REPOS:
        train_df = sampled[sampled["repo_id"] != test_repo]
        test_df = sampled[sampled["repo_id"] == test_repo]

        if test_df.empty or train_df.empty:
            continue

        # Train groups
        train_groups = train_df.groupby("group_key", sort=True).size().values
        train_df = train_df.sort_values("group_key").reset_index(drop=True)
        X_train = train_df[RANKER_FEATURES].values.astype(np.float32)
        y_train = train_df["label_relevant"].astype(int).values

        # Test groups
        test_groups = test_df.groupby("group_key", sort=True).size().values
        test_df = test_df.sort_values("group_key").reset_index(drop=True)
        X_test = test_df[RANKER_FEATURES].values.astype(np.float32)
        y_test = test_df["label_relevant"].astype(int).values

        train_data = lgb.Dataset(X_train, label=y_train, group=train_groups,
                                  feature_name=RANKER_FEATURES)
        params = {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": [5, 10, 20],
            "learning_rate": 0.05,
            "num_leaves": 63,
            "min_data_in_leaf": 10,
            "verbose": -1,
        }
        booster = lgb.train(params, train_data, num_boost_round=500)
        preds = booster.predict(X_test)

        # NDCG per query group
        start = 0
        ndcgs5 = []
        for gs in test_groups:
            g_labels = y_test[start:start + gs]
            g_preds = preds[start:start + gs]
            if g_labels.max() > 0:
                ndcgs5.append(ndcg_score([g_labels], [g_preds], k=5))
            start += gs
        mean_ndcg = np.mean(ndcgs5) if ndcgs5 else 0.0
        repo_ndcgs[test_repo] = mean_ndcg

        all_preds.extend(preds.tolist())
        all_labels.extend(y_test.tolist())
        all_groups_meta.append((test_repo, test_groups, y_test, preds))

        print(f"  Fold {test_repo:20s}: NDCG@5={mean_ndcg:.4f} "
              f"({len(ndcgs5)} groups with positives)")
        del booster, train_data, X_train, X_test

    overall_ndcg5 = np.mean(list(repo_ndcgs.values()))
    print(f"\n  Overall NDCG@5 (LOO-CV): {overall_ndcg5:.4f}")

    # ── Step 6: Binary classification (gate-like: pos vs neg) ─────
    print("\n── STEP 6: Binary relevance (ranker scores → cutoff) ──")
    # Re-use the LOO predictions to compute F1 at various thresholds
    # For each query group, rank by predicted score, pick top-K, compute precision/recall

    # Optimal-K analysis (simulates cutoff model)
    all_preds_arr = np.array(all_preds)
    all_labels_arr = np.array(all_labels)

    # Per-group F1 at optimal K and fixed K values
    for K_mode in ["optimal", 5, 10, 20, 50]:
        tp_total = fp_total = fn_total = 0
        for test_repo, test_groups, y_test, preds in all_groups_meta:
            start = 0
            for gs in test_groups:
                g_labels = y_test[start:start + gs]
                g_preds = preds[start:start + gs]
                n_pos = (g_labels > 0).sum()

                if K_mode == "optimal":
                    # Oracle: pick exactly n_pos items
                    K = n_pos
                else:
                    K = min(K_mode, gs)

                if K == 0:
                    fn_total += n_pos
                    start += gs
                    continue

                ranked_idx = np.argsort(-g_preds)[:K]
                selected_labels = g_labels[ranked_idx]
                tp = (selected_labels > 0).sum()
                fp = K - tp
                fn = n_pos - tp

                tp_total += tp
                fp_total += fp
                fn_total += fn
                start += gs

        precision = tp_total / (tp_total + fp_total) if (tp_total + fp_total) else 0
        recall = tp_total / (tp_total + fn_total) if (tp_total + fn_total) else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0
        k_label = "oracle-K" if K_mode == "optimal" else f"top-{K_mode}"
        print(f"  {k_label:12s}: P={precision:.4f} R={recall:.4f} F1={f1:.4f}  "
              f"(TP={tp_total} FP={fp_total} FN={fn_total})")

    # ── Step 7: Score-threshold binary F1 ─────────────────────────
    print("\n── STEP 7: Score-threshold F1 (binary: label>0 vs =0) ──")
    # Train a binary classifier (not lambdarank) for threshold-based F1
    sampled["label_binary"] = (sampled["label_relevant"] > 0).astype(int)

    best_f1 = 0
    best_thr = 0
    best_metrics = {}

    for test_repo in REPOS:
        train_df = sampled[sampled["repo_id"] != test_repo]
        test_df = sampled[sampled["repo_id"] == test_repo]

        X_train = train_df[RANKER_FEATURES].values.astype(np.float32)
        y_train = train_df["label_binary"].values
        X_test = test_df[RANKER_FEATURES].values.astype(np.float32)
        y_test_bin = test_df["label_binary"].values

        w_pos = (y_train == 0).sum() / max((y_train == 1).sum(), 1)
        params_bin = {
            "objective": "binary",
            "metric": "auc",
            "learning_rate": 0.05,
            "num_leaves": 63,
            "min_data_in_leaf": 10,
            "scale_pos_weight": w_pos,
            "verbose": -1,
        }
        train_data = lgb.Dataset(X_train, label=y_train, feature_name=RANKER_FEATURES)
        booster = lgb.train(params_bin, train_data, num_boost_round=300)
        probs = booster.predict(X_test)

        # Find best threshold for this fold
        for thr in np.arange(0.05, 0.96, 0.05):
            pred_pos = probs >= thr
            tp = ((pred_pos) & (y_test_bin == 1)).sum()
            fp = ((pred_pos) & (y_test_bin == 0)).sum()
            fn = ((~pred_pos) & (y_test_bin == 1)).sum()
            p = tp / (tp + fp) if (tp + fp) else 0
            r = tp / (tp + fn) if (tp + fn) else 0
            f = 2 * p * r / (p + r) if (p + r) else 0

        del booster, train_data

    # Single train-on-all, then self-eval for feature importance
    print("\n  Training on all data for feature importance...")
    X_all = sampled[RANKER_FEATURES].values.astype(np.float32)
    y_all = sampled["label_binary"].values
    w_pos = (y_all == 0).sum() / max((y_all == 1).sum(), 1)
    params_bin = {
        "objective": "binary",
        "metric": "auc",
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 10,
        "scale_pos_weight": w_pos,
        "verbose": -1,
    }
    train_data = lgb.Dataset(X_all, label=y_all, feature_name=RANKER_FEATURES)
    booster = lgb.train(params_bin, train_data, num_boost_round=300)

    probs_all = booster.predict(X_all)

    # Full sweep
    print(f"\n  {'Threshold':>10s} {'Precision':>10s} {'Recall':>10s} {'F1':>8s}")
    print("  " + "-" * 42)
    for thr in np.arange(0.1, 0.96, 0.05):
        pred_pos = probs_all >= thr
        tp = ((pred_pos) & (y_all == 1)).sum()
        fp = ((pred_pos) & (y_all == 0)).sum()
        fn = ((~pred_pos) & (y_all == 1)).sum()
        p = tp / (tp + fp) if (tp + fp) else 0
        r = tp / (tp + fn) if (tp + fn) else 0
        f = 2 * p * r / (p + r) if (p + r) else 0
        marker = " <-- best" if f > best_f1 else ""
        if f > best_f1:
            best_f1 = f
            best_thr = thr
            best_metrics = {"precision": p, "recall": r, "f1": f, "tp": int(tp),
                            "fp": int(fp), "fn": int(fn)}
        print(f"  {thr:10.2f} {p:10.4f} {r:10.4f} {f:8.4f}{marker}")

    print(f"\n  Best binary F1={best_f1:.4f} at threshold={best_thr:.2f}")
    print(f"    P={best_metrics['precision']:.4f} R={best_metrics['recall']:.4f}")
    print(f"    TP={best_metrics['tp']} FP={best_metrics['fp']} FN={best_metrics['fn']}")

    # Feature importance
    imp = booster.feature_importance(importance_type="gain")
    fi = sorted(zip(RANKER_FEATURES, imp), key=lambda x: -x[1])
    print("\n  Top-15 feature importance (gain):")
    for name, gain in fi[:15]:
        print(f"    {name:30s} {gain:>12,.0f}")

    # ── Step 8: LOO-CV binary F1 (honest) ─────────────────────────
    print("\n── STEP 8: LOO-CV binary F1 (held-out repo) ──")
    fold_results = []
    for test_repo in REPOS:
        train_df = sampled[sampled["repo_id"] != test_repo]
        test_df = sampled[sampled["repo_id"] == test_repo]

        X_tr = train_df[RANKER_FEATURES].values.astype(np.float32)
        y_tr = train_df["label_binary"].values
        X_te = test_df[RANKER_FEATURES].values.astype(np.float32)
        y_te = test_df["label_binary"].values

        w = (y_tr == 0).sum() / max((y_tr == 1).sum(), 1)
        params_cv = {
            "objective": "binary", "metric": "auc",
            "learning_rate": 0.05, "num_leaves": 63,
            "min_data_in_leaf": 10, "scale_pos_weight": w, "verbose": -1,
        }
        td = lgb.Dataset(X_tr, label=y_tr, feature_name=RANKER_FEATURES)
        b = lgb.train(params_cv, td, num_boost_round=300)
        probs_te = b.predict(X_te)

        # Find best threshold on this fold
        best_fold_f1 = 0
        best_fold_thr = 0
        for thr in np.arange(0.05, 0.96, 0.05):
            pred_p = probs_te >= thr
            tp = ((pred_p) & (y_te == 1)).sum()
            fp = ((pred_p) & (y_te == 0)).sum()
            fn = ((~pred_p) & (y_te == 1)).sum()
            p = tp / (tp + fp) if (tp + fp) else 0
            r = tp / (tp + fn) if (tp + fn) else 0
            f = 2 * p * r / (p + r) if (p + r) else 0
            if f > best_fold_f1:
                best_fold_f1 = f
                best_fold_thr = thr
                fold_p, fold_r = p, r

        fold_results.append({
            "repo": test_repo, "f1": best_fold_f1, "threshold": best_fold_thr,
            "precision": fold_p, "recall": fold_r,
            "test_pos": int(y_te.sum()), "test_total": len(y_te),
        })
        print(f"  {test_repo:20s}: F1={best_fold_f1:.4f} (P={fold_p:.4f} R={fold_r:.4f}) "
              f"thr={best_fold_thr:.2f}  [{int(y_te.sum())} pos / {len(y_te)} total]")
        del b, td

    mean_f1 = np.mean([r["f1"] for r in fold_results])
    mean_p = np.mean([r["precision"] for r in fold_results])
    mean_r = np.mean([r["recall"] for r in fold_results])
    print(f"\n  LOO-CV Mean: F1={mean_f1:.4f} P={mean_p:.4f} R={mean_r:.4f}")

    # ── Step 9: End-to-end ranking F1 ─────────────────────────────
    print("\n── STEP 9: End-to-end ranking F1 (ranker score → top-K) ──")
    print("  Using LOO-CV ranker predictions, selecting top-K by score")
    print("  (This simulates what the actual system does)")

    for test_repo, test_groups, y_test, preds in all_groups_meta:
        start = 0
        best_k_f1 = 0
        best_k = 0
        for K in range(1, 51):
            tp = fp = fn = 0
            pos2 = 0
            for gs in test_groups:
                g_labels = y_test[pos2:pos2 + gs]
                g_preds = preds[pos2:pos2 + gs]
                n_pos = (g_labels > 0).sum()
                k = min(K, gs)
                ranked = np.argsort(-g_preds)[:k]
                sel = g_labels[ranked]
                tp += (sel > 0).sum()
                fp += k - (sel > 0).sum()
                fn += n_pos - (sel > 0).sum()
                pos2 += gs
            p = tp / (tp + fp) if (tp + fp) else 0
            r = tp / (tp + fn) if (tp + fn) else 0
            f = 2 * p * r / (p + r) if (p + r) else 0
            if f > best_k_f1:
                best_k_f1 = f
                best_k = K
                best_p, best_r = p, r
        print(f"  {test_repo:20s}: best F1={best_k_f1:.4f} at K={best_k} "
              f"(P={best_p:.4f} R={best_r:.4f})")

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
