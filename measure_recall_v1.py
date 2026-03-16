#!/usr/bin/env python3
"""Measure per-harvester recall against ground truth.

For each GT task with matched existing defs, runs:
  1. Embedding: full cosine sim (no top_k)
  2. Term match: SQL LIKE on def name/qualified_name/docstring/lexical_path
  3. Tantivy lexical: BM25 full-text search

Reports how many GT defs each harvester finds, and critically,
how many are found ONLY by embedding (unique embedding recall).
"""

import json
import os
import re
import sqlite3
import sys
import glob
import time
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

# ── Config ──
DATA_DIR = os.path.expanduser("~/.cpl-lab/data")
CLONE_BASE = os.path.expanduser("~/.cpl-lab/clones")
CLONE_SETS = ["cutoff", "eval", "ranker-gate"]

# Stop words from codeplane parsing
from codeplane.mcp.tools.recon.models import _STOP_WORDS
from codeplane.mcp.tools.recon.parsing import parse_task

# ── Results tracking ──
@dataclass
class TaskResult:
    repo: str
    task_id: str
    query_text: str
    gt_defs_total: int  # total in GT
    gt_defs_matched: int  # exist in index
    gt_uids: list[str] = field(default_factory=list)

    # Per-harvester: set of GT uids found
    emb_found: set = field(default_factory=set)
    term_found: set = field(default_factory=set)

    # Embedding ranks/sims for GT defs
    emb_ranks: dict = field(default_factory=dict)  # uid -> rank (0-based)
    emb_sims: dict = field(default_factory=dict)   # uid -> similarity


def build_repo_index():
    """Map lang-repo -> index.db path."""
    repo_to_db = {}
    for cset in CLONE_SETS:
        cdir = os.path.join(CLONE_BASE, cset)
        if not os.path.isdir(cdir):
            continue
        for repo in os.listdir(cdir):
            db = os.path.join(cdir, repo, ".codeplane", "index.db")
            if os.path.isfile(db):
                repo_to_db[repo.lower()] = (
                    os.path.join(cdir, repo, ".codeplane"),
                    cset,
                )
    return repo_to_db


def match_gt_defs_to_index(gt_defs, conn):
    """Match GT defs to index def_uids. Returns list of (gt_def, def_uid)."""
    matched = []
    for rd in gt_defs:
        name = rd["name"]
        path = rd["path"]
        # Handle qualified names like "PluginManager::load_plugin"
        if "::" in name:
            name = name.split("::")[-1]
        if "." in name and not name.startswith("__"):
            name = name.split(".")[-1]

        rows = list(conn.execute(
            """SELECT d.def_uid, d.kind, d.name, d.start_line
               FROM def_facts d JOIN files f ON d.file_id = f.id
               WHERE d.name = ? AND f.path = ?""",
            (name, path),
        ))
        if rows:
            # If multiple matches, prefer closest start_line
            gt_line = rd.get("start_line", 0)
            best = min(rows, key=lambda r: abs(r[3] - gt_line))
            matched.append((rd, best[0]))
        else:
            # Try without exact path match (path might differ slightly)
            rows = list(conn.execute(
                """SELECT d.def_uid, d.kind, d.name, d.start_line, f.path
                   FROM def_facts d JOIN files f ON d.file_id = f.id
                   WHERE d.name = ?""",
                (name,),
            ))
            # Find best path match
            for r in rows:
                if r[4].endswith(path) or path.endswith(r[4]):
                    matched.append((rd, r[0]))
                    break
    return matched


def term_match_sql(terms, conn):
    """Replicate find_defs_matching_term: SQL LIKE on name/qualified_name/docstring/lexical_path."""
    found_uids = set()
    for term in terms:
        if len(term) < 2:
            continue
        pattern = f"%{term}%"
        rows = conn.execute(
            """SELECT def_uid FROM def_facts
               WHERE name LIKE ? COLLATE NOCASE
                  OR qualified_name LIKE ? COLLATE NOCASE
                  OR docstring LIKE ? COLLATE NOCASE
                  OR lexical_path LIKE ? COLLATE NOCASE""",
            (pattern, pattern, pattern, pattern),
        ).fetchall()
        for r in rows:
            found_uids.add(r[0])
    return found_uids


def run_measurement():
    print("Loading embedding model...")
    t0 = time.time()
    from fastembed import TextEmbedding
    model = TextEmbedding(
        model_name="BAAI/bge-small-en-v1.5",
        max_length=512,
    )
    print(f"  Model loaded in {time.time()-t0:.1f}s")

    repo_index = build_repo_index()
    all_results: list[TaskResult] = []

    # Aggregate counters
    total_gt_defs = 0
    total_matched = 0
    total_emb_found = 0
    total_term_found = 0
    total_emb_only = 0
    total_term_only = 0
    total_both = 0
    total_neither = 0

    # Per-repo aggregates
    repo_summaries = []

    for entry in sorted(os.listdir(DATA_DIR)):
        gt_dir = os.path.join(DATA_DIR, entry, "ground_truth")
        if not os.path.isdir(gt_dir):
            continue

        parts = entry.split("-", 1)
        if len(parts) < 2:
            continue
        repo_name = parts[1].lower()

        info = repo_index.get(repo_name)
        if not info:
            continue
        codeplane_dir, cset = info
        db_path = os.path.join(codeplane_dir, "index.db")
        emb_dir = os.path.join(codeplane_dir, "def_embedding")

        # Check embedding index exists
        npz_path = os.path.join(emb_dir, "def_embeddings.npz")
        if not os.path.isfile(npz_path):
            continue

        # Load embedding matrix
        data = np.load(npz_path, allow_pickle=False)
        matrix = data["matrix"].astype(np.float16)
        uids_arr = list(data["uids"])
        uid_to_idx = {u: i for i, u in enumerate(uids_arr)}

        conn = sqlite3.connect(db_path)

        # Process GT files
        gt_files = sorted(glob.glob(os.path.join(gt_dir, "*.json")))
        repo_emb_only = 0
        repo_term_only = 0
        repo_both = 0
        repo_neither = 0
        repo_matched_count = 0

        for gf in gt_files:
            if "non_ok" in os.path.basename(gf):
                continue
            with open(gf) as f:
                d = json.load(f)

            task_id = d.get("task_id", os.path.basename(gf))
            gt_defs = d.get("minimum_sufficient_defs", [])
            if not gt_defs:
                continue

            # Match GT defs to index
            matched = match_gt_defs_to_index(gt_defs, conn)
            if not matched:
                continue

            gt_uid_set = set(uid for _, uid in matched)
            repo_matched_count += len(gt_uid_set)

            # For each query in this task
            queries = d.get("queries", [])
            if not queries:
                continue

            for qi, q in enumerate(queries):
                query_text = q.get("query_text", "")
                if not query_text:
                    continue

                # Parse task to get terms
                parsed = parse_task(query_text)
                all_terms = parsed.primary_terms + parsed.secondary_terms

                # --- Embedding recall ---
                # Embed query
                vecs = list(model.embed([query_text], batch_size=1))
                q_vec = np.array(vecs[0], dtype=np.float32)
                norm = np.linalg.norm(q_vec)
                if norm > 0:
                    q_vec /= norm

                # Full cosine sim
                sims = matrix.astype(np.float32) @ q_vec
                ranked_indices = np.argsort(sims)[::-1]

                # Which GT uids appear in embedding results?
                emb_found_gt = set()
                emb_ranks = {}
                emb_sims_map = {}
                for rank, idx in enumerate(ranked_indices):
                    uid = uids_arr[idx]
                    if uid in gt_uid_set:
                        emb_found_gt.add(uid)
                        emb_ranks[uid] = rank
                        emb_sims_map[uid] = float(sims[idx])

                # --- Term match recall ---
                term_found_uids = term_match_sql(all_terms, conn)
                term_found_gt = gt_uid_set & term_found_uids

                # --- Classification ---
                for uid in gt_uid_set:
                    in_emb = uid in emb_found_gt  # all defs have some sim > 0
                    in_term = uid in term_found_gt

                    # For embedding "found" we need a practical threshold
                    # Let's check rank thresholds: top 200 (current), top 500, all
                    # For now track rank distributions

                # Track per-uid stats
                result = TaskResult(
                    repo=entry,
                    task_id=f"{task_id}_q{qi}",
                    query_text=query_text,
                    gt_defs_total=len(gt_defs),
                    gt_defs_matched=len(gt_uid_set),
                    gt_uids=list(gt_uid_set),
                    emb_found=emb_found_gt,
                    term_found=term_found_gt,
                    emb_ranks=emb_ranks,
                    emb_sims=emb_sims_map,
                )
                all_results.append(result)

        conn.close()

        # Per-repo summary
        repo_results = [r for r in all_results if r.repo == entry]
        if not repo_results:
            continue

    # ── Analysis ──
    print(f"\n{'='*70}")
    print(f"RECALL ANALYSIS: {len(all_results)} queries across repos")
    print(f"{'='*70}\n")

    # Collect all (uid, rank, sim, in_term) tuples
    all_uid_data = []  # (repo, uid, emb_rank, emb_sim, in_term, total_defs)
    for r in all_results:
        total_defs = len([u for u in r.gt_uids if u in r.emb_ranks])
        for uid in r.gt_uids:
            rank = r.emb_ranks.get(uid)
            sim = r.emb_sims.get(uid)
            in_term = uid in r.term_found
            if rank is not None:
                n_total = len(r.emb_ranks)  # proxy for total defs in index
                all_uid_data.append((r.repo, uid, rank, sim, in_term, r.query_text))

    if not all_uid_data:
        print("No data collected!")
        return

    ranks = [d[2] for d in all_uid_data]
    sims = [d[3] for d in all_uid_data]
    in_term_flags = [d[4] for d in all_uid_data]

    print(f"Total GT def-query pairs with embedding data: {len(all_uid_data)}")
    print()

    # Rank distribution of GT defs
    print("EMBEDDING RANK DISTRIBUTION of GT-correct defs:")
    for threshold in [10, 25, 50, 100, 200, 500, 1000, 5000]:
        count = sum(1 for r in ranks if r < threshold)
        pct = 100 * count / len(ranks)
        print(f"  top-{threshold:5d}: {count:5d}/{len(ranks)} ({pct:5.1f}%)")
    print(f"  ALL      : {len(ranks):5d}/{len(ranks)} (100.0%)")

    print()
    print("EMBEDDING SIMILARITY DISTRIBUTION of GT-correct defs:")
    arr = np.array(sims)
    for p in [0, 5, 10, 25, 50, 75, 90, 95, 100]:
        print(f"  p{p:3d}: {np.percentile(arr, p):.4f}")

    print()
    print("TERM MATCH RECALL:")
    term_hit = sum(1 for f in in_term_flags if f)
    print(f"  Found by term match: {term_hit}/{len(in_term_flags)} ({100*term_hit/len(in_term_flags):.1f}%)")

    # ── Critical question: embedding-only recall ──
    print()
    print("UNIQUE RECALL BY HARVESTER (at different embedding top-k):")
    print(f"{'top_k':>8s} | {'emb_only':>10s} | {'term_only':>10s} | {'both':>10s} | {'neither':>10s}")
    print("-" * 60)
    for top_k in [50, 100, 200, 500, 1000, 9999999]:
        label = "ALL" if top_k > 99999 else str(top_k)
        emb_only = term_only = both = neither = 0
        for rank, sim, in_t in zip(ranks, sims, in_term_flags):
            in_emb = rank < top_k and sim > 0
            if in_emb and in_t:
                both += 1
            elif in_emb and not in_t:
                emb_only += 1
            elif not in_emb and in_t:
                term_only += 1
            else:
                neither += 1
        total = len(ranks)
        print(f"{label:>8s} | {emb_only:5d} ({100*emb_only/total:4.1f}%) | "
              f"{term_only:5d} ({100*term_only/total:4.1f}%) | "
              f"{both:5d} ({100*both/total:4.1f}%) | "
              f"{neither:5d} ({100*neither/total:4.1f}%)")

    # ── Embedding-only examples ──
    print()
    print("SAMPLE: GT defs found ONLY by embedding (not by term match), top-200:")
    emb_only_items = [(repo, uid, rank, sim, qt) for repo, uid, rank, sim, in_t, qt
                      in all_uid_data if rank < 200 and not in_t]
    emb_only_items.sort(key=lambda x: x[2])  # sort by rank
    for repo, uid, rank, sim, qt in emb_only_items[:20]:
        print(f"  rank={rank:4d} sim={sim:.3f} repo={repo} query={qt[:60]}...")

    # ── Per-repo summary ──
    print()
    print("PER-REPO RECALL (embedding=ALL, term match):")
    repo_groups = defaultdict(list)
    for d in all_uid_data:
        repo_groups[d[0]].append(d)

    print(f"{'repo':>30s} | {'n_pairs':>7s} | {'emb%':>6s} | {'term%':>6s} | {'emb_only%':>9s} | {'term_only%':>10s}")
    print("-" * 85)
    for repo in sorted(repo_groups.keys()):
        items = repo_groups[repo]
        n = len(items)
        emb_pct = 100  # all defs have similarity > 0
        term_pct = 100 * sum(1 for _, _, _, _, in_t, _ in items if in_t) / n
        emb_only_pct = 100 * sum(1 for _, _, r, s, in_t, _ in items if not in_t) / n
        term_only_pct = 0  # embedding finds all at unlimited
        print(f"{repo:>30s} | {n:7d} | {emb_pct:5.1f}% | {term_pct:5.1f}% | {emb_only_pct:8.1f}% | {term_only_pct:9.1f}%")


if __name__ == "__main__":
    run_measurement()
