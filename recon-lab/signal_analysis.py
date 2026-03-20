"""Signal analysis: measure discriminative power of each raw signal.

For each candidate, we have raw signal features from the pipeline.
Compare feature distributions for GT-relevant vs irrelevant candidates
to quantify which signals actually separate them.
"""

import asyncio
import gc
import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

logging.disable(logging.WARNING)

from coderecon.mcp.context import AppContext
from coderecon.mcp.tools.recon.raw_signals import raw_signals_pipeline
from coderecon.ranking.features import extract_ranker_features

CLONE_DIR = Path(os.path.expanduser("~/.recon/recon-lab/clones/eval"))
DATA_DIR = Path(os.path.expanduser("~/.recon/recon-lab/data"))

REPO_CLONE_MAP = {}
for d in CLONE_DIR.iterdir():
    if d.is_dir() and (d / ".recon" / "index.db").exists():
        REPO_CLONE_MAP[d.name.lower()] = d.name


def find_clone(repo_name):
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


def def_key(c):
    return f"{c.get('path', '')}:{c.get('kind', '')}:{c.get('name', '')}:{c.get('start_line', 0)}"


def filter_gt_to_indexed(gt_defs, db_path):
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
            for r in rows:
                indexed.add(f"{path}:{r[0]}:{r[1]}:{r[2]}")
    conn.close()
    return indexed


def main():
    # Raw signal fields from candidates
    SIGNAL_FIELDS = [
        "emb_score", "emb_rank",
        "term_match_count", "term_total_matches",
        "graph_seed_rank",
        "retriever_hits",
        "hub_score",
        "object_size_lines", "nesting_depth",
        "has_docstring", "has_decorators", "has_return_type",
        "is_test", "has_parent_scope",
    ]

    # Collect signal values for relevant vs irrelevant
    relevant_vals = defaultdict(list)
    irrelevant_vals = defaultdict(list)

    # Also track: how many relevant defs have ANY non-zero signal
    relevant_signal_profile = defaultdict(int)  # field -> count of relevant defs with nonzero value
    total_relevant = 0
    total_irrelevant = 0

    # Track per-query: rank of relevant defs, and what signal found them
    retriever_hits_distribution = defaultdict(int)  # hits bucket -> count

    QTYPES = {"Q_SEMANTIC", "Q_IDENTIFIER", "Q_STRUCTURAL", "Q_FULL"}
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ctx = None
    cached_repo = None
    queries_done = 0

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

        task_files = sorted(gt_dir.glob("M*.json"))[:5]
        for task_file in task_files:
            with open(task_file) as f:
                gt = json.load(f)
            min_defs = gt.get("minimum_sufficient_defs", [])
            gt_keys = filter_gt_to_indexed(min_defs, db_path)
            if not gt_keys:
                continue

            for q in gt.get("queries", []):
                if q.get("query_type") not in QTYPES:
                    continue

                raw = loop.run_until_complete(
                    raw_signals_pipeline(
                        ctx, q["query_text"],
                        seeds=q.get("seeds") or None,
                        pins=q.get("pins") or None,
                    )
                )
                candidates = raw.get("candidates", [])
                queries_done += 1

                for c in candidates:
                    key = def_key(c)
                    is_rel = key in gt_keys

                    if is_rel:
                        total_relevant += 1
                    else:
                        total_irrelevant += 1

                    for field in SIGNAL_FIELDS:
                        val = c.get(field, 0)
                        if val is None:
                            val = 0
                        if isinstance(val, bool):
                            val = int(val)

                        if is_rel:
                            relevant_vals[field].append(val)
                            if val != 0:
                                relevant_signal_profile[field] += 1
                        else:
                            irrelevant_vals[field].append(val)

                    # Track retriever_hits for relevant defs
                    if is_rel:
                        hits = c.get("retriever_hits", 0) or 0
                        retriever_hits_distribution[hits] += 1

                    # Track symbol_source for relevant
                    if is_rel:
                        src = c.get("symbol_source", "none")
                        relevant_signal_profile[f"source:{src}"] += 1

        print(f"  {repo_name}: {queries_done} queries so far", flush=True)

    if ctx is not None:
        ctx.coordinator.close()

    # Compute statistics
    def stats(vals):
        if not vals:
            return {"mean": 0, "median": 0, "p90": 0, "p99": 0, "nonzero_pct": 0}
        s = sorted(vals)
        n = len(s)
        nz = sum(1 for v in s if v != 0)
        return {
            "mean": sum(s) / n,
            "median": s[n // 2],
            "p90": s[int(n * 0.9)],
            "p99": s[int(n * 0.99)],
            "nonzero_pct": nz / n * 100,
        }

    print(f"\n{'=' * 100}")
    print(f"SIGNAL ANALYSIS — {queries_done} queries, {total_relevant} relevant candidates, {total_irrelevant} irrelevant")
    print(f"{'=' * 100}")

    print(f"\n{'SIGNAL':<25} {'RELEVANT mean':>13} {'IRREL mean':>11} {'RATIO':>7} {'REL nonzero%':>13} {'IRREL nonzero%':>15} {'REL median':>11} {'IRREL median':>13}")
    print("-" * 110)
    for field in SIGNAL_FIELDS:
        rs = stats(relevant_vals[field])
        irs = stats(irrelevant_vals[field])
        ratio = rs["mean"] / irs["mean"] if irs["mean"] != 0 else float("inf") if rs["mean"] != 0 else 1.0
        print(f"  {field:<23} {rs['mean']:>12.4f} {irs['mean']:>11.4f} {ratio:>7.2f}x {rs['nonzero_pct']:>12.1f}% {irs['nonzero_pct']:>14.1f}% {rs['median']:>11.4f} {irs['median']:>12.4f}")

    print(f"\n{'─' * 80}")
    print("RELEVANT DEF SIGNAL COVERAGE (what % of relevant defs have each signal)")
    print(f"{'─' * 80}")
    for field in SIGNAL_FIELDS:
        count = relevant_signal_profile.get(field, 0)
        pct = count / total_relevant * 100 if total_relevant > 0 else 0
        bar = "█" * int(pct / 2)
        print(f"  {field:<25} {count:>5}/{total_relevant}  ({pct:>5.1f}%)  {bar}")

    print(f"\n{'─' * 80}")
    print("RETRIEVER HITS DISTRIBUTION FOR RELEVANT DEFS")
    print(f"{'─' * 80}")
    for hits in sorted(retriever_hits_distribution):
        count = retriever_hits_distribution[hits]
        pct = count / total_relevant * 100 if total_relevant > 0 else 0
        bar = "█" * int(pct / 2)
        print(f"  hits={hits:<3}  {count:>5}  ({pct:>5.1f}%)  {bar}")

    print(f"\n{'─' * 80}")
    print("SYMBOL SOURCE FOR RELEVANT DEFS")
    print(f"{'─' * 80}")
    for k, v in sorted(relevant_signal_profile.items()):
        if k.startswith("source:"):
            src = k[7:]
            pct = v / total_relevant * 100 if total_relevant > 0 else 0
            print(f"  {src:<25} {v:>5}  ({pct:>5.1f}%)")


if __name__ == "__main__":
    main()
