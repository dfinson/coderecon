"""Vibe check: baseline vs ranking, filtering GT to only indexable defs.

Runs across all eval repos with indexes. For each task+query, computes
hit@K and NDCG using ONLY GT defs that actually exist in the index
(filtering out phantom defs). This gives an honest recall ceiling of 100%.
"""

import asyncio
import gc
import json
import logging
import math
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

logging.disable(logging.WARNING)

from coderecon.mcp.context import AppContext
from coderecon.mcp.tools.recon.raw_signals import raw_signals_pipeline
from coderecon.ranking.cutoff import load_cutoff
from coderecon.ranking.features import (
    extract_cutoff_features,
    extract_gate_features,
    extract_ranker_features,
)
from coderecon.ranking.gate import load_gate
from coderecon.ranking.models import GateLabel
from coderecon.ranking.ranker import load_ranker

CLONE_DIR = Path(os.path.expanduser("~/.cpl-lab/clones/eval"))
MODELS_DIR = Path(os.path.expanduser("~/.cpl-lab/models"))
DATA_DIR = Path(os.path.expanduser("~/.cpl-lab/data"))

# Repo manifest: data dir name -> clone dir name
REPO_CLONE_MAP = {}
for d in CLONE_DIR.iterdir():
    if d.is_dir() and (d / ".recon" / "index.db").exists():
        REPO_CLONE_MAP[d.name.lower()] = d.name


def find_clone(repo_name: str) -> str | None:
    parts = repo_name.split("-", 1)
    name = parts[1] if len(parts) == 2 else repo_name
    if repo_name.lower() in REPO_CLONE_MAP:
        return REPO_CLONE_MAP[repo_name.lower()]
    if name.lower() in REPO_CLONE_MAP:
        return REPO_CLONE_MAP[name.lower()]
    for cn in REPO_CLONE_MAP:
        if name.lower() in cn or cn in name.lower():
            return REPO_CLONE_MAP[cn]
    return None


def def_key(c: dict) -> str:
    return f"{c.get('path', '')}:{c.get('kind', '')}:{c.get('name', '')}:{c.get('start_line', 0)}"


def filter_gt_to_indexed(gt_defs: list[dict], db_path: Path) -> set[str]:
    """Return only GT def keys that actually exist in the index."""
    conn = sqlite3.connect(str(db_path))
    indexed = set()
    for d in gt_defs:
        path, name = d["path"], d["name"]
        frows = conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchall()
        if not frows:
            continue
        rows = conn.execute(
            "SELECT kind, name, start_line FROM def_facts WHERE file_id=? AND name=?",
            (frows[0][0], name),
        ).fetchall()
        if rows:
            # Use the index's actual kind/start_line for the key
            for r in rows:
                indexed.add(f"{path}:{r[0]}:{r[1]}:{r[2]}")
    conn.close()
    return indexed


def dcg(ranks: list[int], k: int) -> float:
    """Compute DCG@k given a list of 1-based ranks of relevant items."""
    s = 0.0
    for r in ranks:
        if r <= k:
            s += 1.0 / math.log2(r + 1)
    return s


def ndcg(ranks: list[int], n_relevant: int, k: int) -> float:
    """NDCG@k: DCG of actual ranks / ideal DCG (all relevant at top)."""
    if n_relevant == 0:
        return 1.0  # vacuous
    ideal_ranks = list(range(1, n_relevant + 1))
    ideal = dcg(ideal_ranks, k)
    if ideal == 0:
        return 0.0
    return dcg(ranks, k) / ideal


def run_pipeline(ctx, loop, query_text, seeds, pins, gate, ranker, cutoff):
    raw = loop.run_until_complete(
        raw_signals_pipeline(ctx, query_text, seeds=seeds, pins=pins)
    )
    candidates = raw.get("candidates", [])
    query_features = raw.get("query_features", {})
    repo_features = raw.get("repo_features", {})

    gate_feats = extract_gate_features(candidates, query_features, repo_features)
    gate_label = gate.classify(gate_feats)

    if gate_label != GateLabel.OK:
        return [], 0, gate_label, len(candidates)

    ranker_feats = extract_ranker_features(candidates, query_features)
    scores = ranker.score(ranker_feats)
    scored = sorted(zip(candidates, scores), key=lambda x: -x[1])

    ranked_for_cutoff = [{**c, "ranker_score": s} for c, s in scored]
    cutoff_feats = extract_cutoff_features(ranked_for_cutoff, query_features, repo_features)
    predicted_n = cutoff.predict(cutoff_feats)

    return scored, predicted_n, gate_label, len(candidates)


def main():
    _dummy = Path("/dev/null/no_model.lgbm")
    baseline_models = {
        "gate": load_gate(_dummy),
        "ranker": load_ranker(_dummy),
        "cutoff": load_cutoff(_dummy),
    }
    ranking_models = {
        "gate": load_gate(MODELS_DIR / "gate.lgbm"),
        "ranker": load_ranker(MODELS_DIR / "ranker.lgbm"),
        "cutoff": load_cutoff(MODELS_DIR / "cutoff.lgbm"),
    }

    # Query types to test (sample for speed)
    QTYPES = {"Q_SEMANTIC", "Q_IDENTIFIER", "Q_STRUCTURAL", "Q_FULL"}

    # Accumulate metrics
    all_metrics = {"baseline": defaultdict(list), "ranking": defaultdict(list)}
    by_qtype = {
        "baseline": defaultdict(lambda: defaultdict(list)),
        "ranking": defaultdict(lambda: defaultdict(list)),
    }
    by_repo = {
        "baseline": defaultdict(lambda: defaultdict(list)),
        "ranking": defaultdict(lambda: defaultdict(list)),
    }

    # Track summary
    total_queries = 0
    skipped_no_gt = 0
    skipped_gate = {"baseline": 0, "ranking": 0}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ctx = None
    cached_repo = None

    repos_done = 0
    for repo_dir in sorted(DATA_DIR.iterdir()):
        gt_dir = repo_dir / "ground_truth"
        if not gt_dir.exists():
            continue
        repo_name = repo_dir.name
        clone_name = find_clone(repo_name)
        if clone_name is None:
            continue

        clone_path = CLONE_DIR / clone_name
        db_path = clone_path / ".recon" / "index.db"
        if not db_path.exists():
            continue

        # Load context for this repo
        if cached_repo != clone_name:
            if ctx is not None:
                ctx.coordinator.close()
                ctx = None
                gc.collect()

            cp = clone_path / ".recon"
            ctx = AppContext.create(
                repo_root=clone_path,
                db_path=cp / "index.db",
                tantivy_path=cp / "tantivy",
            )
            loop.run_until_complete(ctx.coordinator.load_existing())
            cached_repo = clone_name

        # Process tasks (sample: first 5 tasks per repo for speed)
        task_files = sorted(gt_dir.glob("M*.json"))[:5]
        repo_query_count = 0

        for task_file in task_files:
            with open(task_file) as f:
                gt = json.load(f)

            min_defs = gt.get("minimum_sufficient_defs", [])
            gt_keys = filter_gt_to_indexed(min_defs, db_path)

            if not gt_keys:
                skipped_no_gt += 1
                continue

            queries = gt.get("queries", [])
            for q in queries:
                qtype = q.get("query_type", "")
                if qtype not in QTYPES:
                    continue

                query_text = q.get("query_text", "")
                seeds = q.get("seeds") or None
                pins = q.get("pins") or None
                total_queries += 1
                repo_query_count += 1

                for mode_name, models in [("baseline", baseline_models), ("ranking", ranking_models)]:
                    scored, pred_n, gate_label, n_cands = run_pipeline(
                        ctx, loop, query_text, seeds, pins,
                        models["gate"], models["ranker"], models["cutoff"],
                    )

                    if gate_label != GateLabel.OK:
                        skipped_gate[mode_name] += 1
                        continue

                    all_keys = [def_key(c) for c, _ in scored]

                    # Find ranks of indexed GT defs
                    ranks = []
                    for gk in gt_keys:
                        if gk in all_keys:
                            ranks.append(all_keys.index(gk) + 1)

                    n_rel = len(gt_keys)
                    hit5 = sum(1 for r in ranks if r <= 5) / n_rel
                    hit10 = sum(1 for r in ranks if r <= 10) / n_rel
                    hit20 = sum(1 for r in ranks if r <= 20) / n_rel
                    hit_n = sum(1 for r in ranks if r <= pred_n) / n_rel
                    ndcg5 = ndcg(ranks, n_rel, 5)
                    ndcg10 = ndcg(ranks, n_rel, 10)
                    ndcg20 = ndcg(ranks, n_rel, 20)

                    # Cutoff metrics
                    top_n_keys = set(all_keys[:pred_n])
                    tp = len(gt_keys & top_n_keys)
                    prec = tp / pred_n if pred_n > 0 else 0
                    rec = tp / n_rel if n_rel > 0 else 0
                    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0

                    # Fixed cutoff recall sweep
                    fixed_cutoff_recall = {}
                    for fc in [5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 500, 1000, 2000, 5000]:
                        top_fc_keys = set(all_keys[:fc])
                        fc_tp = len(gt_keys & top_fc_keys)
                        fixed_cutoff_recall[f"rec@{fc}"] = fc_tp / n_rel if n_rel > 0 else 0
                    # Also track: how many GT defs are NOT in candidates at all?
                    gt_in_candidates = len([gk for gk in gt_keys if gk in set(all_keys)])
                    fixed_cutoff_recall["gt_in_cands"] = gt_in_candidates / n_rel if n_rel > 0 else 0
                    fixed_cutoff_recall["n_cands"] = len(all_keys)

                    metrics = {
                        "ndcg5": ndcg5, "ndcg10": ndcg10, "ndcg20": ndcg20,
                        "hit5": hit5, "hit10": hit10, "hit20": hit20,
                        "hit_n": hit_n, "pred_n": pred_n,
                        "cutoff_prec": prec, "cutoff_rec": rec, "cutoff_f1": f1,
                        "n_gt": n_rel, "n_found": len(ranks),
                        **fixed_cutoff_recall,
                    }
                    for k, v in metrics.items():
                        all_metrics[mode_name][k].append(v)
                        by_qtype[mode_name][qtype][k].append(v)
                        by_repo[mode_name][repo_name][k].append(v)

        repos_done += 1
        print(f"  [{repos_done}] {repo_name}: {repo_query_count} queries", flush=True)

    if ctx is not None:
        ctx.coordinator.close()

    # Print results
    print(f"\n{'=' * 80}")
    print(f"FILTERED VIBE CHECK — {total_queries} queries, {repos_done} repos")
    print(f"(GT filtered to only indexable defs — recall ceiling = 100%)")
    print(f"Skipped {skipped_no_gt} tasks with 0 indexable GT defs")
    print(f"{'=' * 80}")

    def avg(lst):
        return sum(lst) / len(lst) if lst else 0

    print(f"\n{'─' * 80}")
    print(f"{'OVERALL':40s} {'BASELINE':>18s} {'RANKING':>18s}")
    print(f"{'─' * 80}")
    for k in ["ndcg5", "ndcg10", "ndcg20", "hit5", "hit10", "hit20", "hit_n",
              "cutoff_prec", "cutoff_rec", "cutoff_f1", "pred_n"]:
        b = avg(all_metrics["baseline"][k])
        r = avg(all_metrics["ranking"][k])
        delta = r - b
        arrow = "▲" if delta > 0.001 else ("▼" if delta < -0.001 else "─")
        print(f"  {k:38s} {b:>8.4f}           {r:>8.4f}  {arrow} {delta:+.4f}")

    print(f"\n{'─' * 80}")
    print(f"{'BY QUERY TYPE':40s} {'BASELINE ndcg@5':>15s} {'RANKING ndcg@5':>15s} {'Δ':>8s}")
    print(f"{'─' * 80}")
    for qt in sorted(QTYPES):
        b = avg(by_qtype["baseline"][qt]["ndcg5"])
        r = avg(by_qtype["ranking"][qt]["ndcg5"])
        n = len(by_qtype["baseline"][qt]["ndcg5"])
        delta = r - b
        arrow = "▲" if delta > 0.001 else ("▼" if delta < -0.001 else "─")
        print(f"  {qt:28s} n={n:4d}  {b:>8.4f}        {r:>8.4f}   {arrow} {delta:+.4f}")

    print(f"\n{'─' * 80}")
    print(f"{'BY REPO':40s} {'BASE ndcg@5':>12s} {'RANK ndcg@5':>12s} {'BASE hit@5':>11s} {'RANK hit@5':>11s}")
    print(f"{'─' * 80}")
    for repo in sorted(by_repo["baseline"]):
        bn = avg(by_repo["baseline"][repo]["ndcg5"])
        rn = avg(by_repo["ranking"][repo]["ndcg5"])
        bh = avg(by_repo["baseline"][repo]["hit5"])
        rh = avg(by_repo["ranking"][repo]["hit5"])
        n = len(by_repo["baseline"][repo]["ndcg5"])
        print(f"  {repo:30s} n={n:3d} {bn:>8.4f}     {rn:>8.4f}     {bh:>8.4f}     {rh:>8.4f}")

    print(f"\nGate filtered: baseline={skipped_gate['baseline']}, ranking={skipped_gate['ranking']}")

    print(f"\n{'─' * 80}")
    print(f"{'RECALL @ FIXED CUTOFF':40s} {'BASELINE':>18s} {'RANKING':>18s}")
    print(f"{'─' * 80}")
    for k in ["rec@5", "rec@10", "rec@20", "rec@30", "rec@50", "rec@75", "rec@100",
               "rec@150", "rec@200", "rec@300", "rec@500", "rec@1000", "rec@2000", "rec@5000",
               "gt_in_cands"]:
        b = avg(all_metrics["baseline"][k])
        r = avg(all_metrics["ranking"][k])
        delta = r - b
        arrow = "▲" if delta > 0.001 else ("▼" if delta < -0.001 else "─")
        print(f"  {k:38s} {b:>8.4f}           {r:>8.4f}  {arrow} {delta:+.4f}")


if __name__ == "__main__":
    main()
