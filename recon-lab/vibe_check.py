"""Vibe check: compare baseline vs ranking on celery GT queries."""

import asyncio
import gc
import json
import logging
import sys
from pathlib import Path

# Suppress noisy logs during loading
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

CLONE_DIR = Path("~/.cpl-lab/clones/eval/celery").expanduser()
MODELS_DIR = Path("~/.cpl-lab/models").expanduser()
GT_DIR = Path("~/.cpl-lab/data/python-celery/ground_truth").expanduser()


def def_key(c: dict) -> str:
    return f"{c.get('path', '')}:{c.get('kind', '')}:{c.get('name', '')}:{c.get('start_line', 0)}"


def load_gt_queries(task_file: str) -> tuple[list[dict], set[str]]:
    with open(GT_DIR / task_file) as f:
        gt = json.load(f)
    gt_keys = {
        f"{d['path']}:{d['kind']}:{d['name']}:{d['start_line']}"
        for d in gt.get("minimum_sufficient_defs", [])
    }
    queries = []
    for q in gt["queries"]:
        queries.append({
            "query_type": q["query_type"],
            "query_text": q["query_text"],
            "seeds": q.get("seeds") or None,
            "pins": q.get("pins") or None,
        })
    return queries, gt_keys


def run_pipeline(ctx, loop, query_text, seeds, pins, gate, ranker, cutoff):
    """Run the full recon pipeline and return results."""
    raw = loop.run_until_complete(
        raw_signals_pipeline(ctx, query_text, seeds=seeds, pins=pins)
    )
    candidates = raw.get("candidates", [])
    query_features = raw.get("query_features", {})
    repo_features = raw.get("repo_features", {})

    # Gate
    gate_feats = extract_gate_features(candidates, query_features, repo_features)
    gate_label = gate.classify(gate_feats)

    if gate_label != GateLabel.OK:
        return [], 0, gate_label, len(candidates)

    # Rank
    ranker_feats = extract_ranker_features(candidates, query_features)
    scores = ranker.score(ranker_feats)
    scored = sorted(zip(candidates, scores), key=lambda x: -x[1])

    # Cutoff
    ranked_for_cutoff = [{**c, "ranker_score": s} for c, s in scored]
    cutoff_feats = extract_cutoff_features(
        ranked_for_cutoff, query_features, repo_features
    )
    predicted_n = cutoff.predict(cutoff_feats)

    return scored, predicted_n, gate_label, len(candidates)


def main():
    # Load context
    cp = CLONE_DIR / ".recon"
    ctx = AppContext.create(
        repo_root=CLONE_DIR,
        db_path=cp / "index.db",
        tantivy_path=cp / "tantivy",
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ctx.coordinator.load_existing())

    # Load models for both modes
    _dummy = Path("/dev/null/no_model.lgbm")
    baseline = {
        "gate": load_gate(_dummy),
        "ranker": load_ranker(_dummy),
        "cutoff": load_cutoff(_dummy),
    }
    ranking = {
        "gate": load_gate(MODELS_DIR / "gate.lgbm"),
        "ranker": load_ranker(MODELS_DIR / "ranker.lgbm"),
        "cutoff": load_cutoff(MODELS_DIR / "cutoff.lgbm"),
    }
    print(f"Ranker available: {ranking['ranker'].is_available}")
    print(f"Gate available:   {ranking['gate'].is_available}")
    print(f"Cutoff available: {ranking['cutoff'].is_available}")
    print()

    # Load GT tasks
    tasks = []
    for tf in sorted(GT_DIR.glob("M*.json")):
        queries, gt_keys = load_gt_queries(tf.name)
        tasks.append((tf.name, queries, gt_keys))

    # Run queries for M1 and M2 (pick 2 query types each to keep it short)
    QTYPES_TO_TEST = {"Q_SEMANTIC", "Q_IDENTIFIER", "Q_STRUCTURAL", "Q_FULL"}
    TOP_K = 20

    for task_name, queries, gt_keys in tasks[:2]:
        print(f"{'=' * 70}")
        print(f"TASK: {task_name}  ({len(gt_keys)} ground truth defs)")
        print(f"GT: {gt_keys}")
        print(f"{'=' * 70}")

        for q in queries:
            if q["query_type"] not in QTYPES_TO_TEST:
                continue

            print(f"\n  [{q['query_type']}] {q['query_text'][:90]}")
            if q["seeds"]:
                print(f"    seeds: {q['seeds']}")

            for mode_name, models in [("BASELINE", baseline), ("RANKING", ranking)]:
                scored, pred_n, gate_label, n_cands = run_pipeline(
                    ctx, loop,
                    q["query_text"], q["seeds"], q["pins"],
                    models["gate"], models["ranker"], models["cutoff"],
                )

                if gate_label != GateLabel.OK:
                    print(f"    {mode_name}: gate={gate_label.value}, skipped")
                    continue

                # Compute hits in top-K
                top_keys = [def_key(c) for c, _ in scored[:TOP_K]]
                hits = gt_keys & set(top_keys)

                # Find rank of each GT def
                all_keys = [def_key(c) for c, _ in scored]
                gt_ranks = {}
                for gk in gt_keys:
                    if gk in all_keys:
                        gt_ranks[gk] = all_keys.index(gk) + 1
                    else:
                        gt_ranks[gk] = -1  # not in candidates

                hit5 = len(gt_keys & set(top_keys[:5]))
                hit10 = len(gt_keys & set(top_keys[:10]))
                hit20 = len(gt_keys & set(top_keys[:20]))

                print(f"    {mode_name}: gate={gate_label.value}  "
                      f"pred_n={pred_n}  candidates={n_cands}  "
                      f"hit@5={hit5}/{len(gt_keys)}  "
                      f"hit@10={hit10}/{len(gt_keys)}  "
                      f"hit@20={hit20}/{len(gt_keys)}")

                # Show where GT defs rank
                for gk, rank in sorted(gt_ranks.items(), key=lambda x: x[1] if x[1] > 0 else 99999):
                    short = gk.split("/")[-1] if "/" in gk else gk
                    marker = " ✓" if rank <= pred_n and rank > 0 else ""
                    if rank > 0:
                        score = scored[rank - 1][1]
                        print(f"      rank {rank:4d} (score {score:.4f}) {short}{marker}")
                    else:
                        print(f"      NOT IN CANDIDATES: {short}")

    ctx.coordinator.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
