#!/usr/bin/env python3
"""Measure per-harvester recall against ground truth.

For each GT def × query pair:
  1. Embedding: full cosine sim, record rank among all defs
  2. Term match: SQL LIKE on name/qualified_name/lexical_path

Reports rank distribution of GT defs and unique recall per harvester.
~24,875 queries across ~90 repos. ETA: ~15 min.
"""

import json
import os
import sqlite3
import sys
import glob
import time
from collections import defaultdict

import numpy as np

from codeplane.mcp.tools.recon.parsing import parse_task

DATA_DIR = os.path.expanduser("~/.cpl-lab/data")
CLONE_BASE = os.path.expanduser("~/.cpl-lab/clones")
CLONE_SETS = ["cutoff", "eval", "ranker-gate"]


def build_repo_index():
    repo_to_dir = {}
    for cset in CLONE_SETS:
        cdir = os.path.join(CLONE_BASE, cset)
        if not os.path.isdir(cdir):
            continue
        for repo in os.listdir(cdir):
            db = os.path.join(cdir, repo, ".codeplane", "index.db")
            if os.path.isfile(db):
                repo_to_dir[repo.lower()] = os.path.join(cdir, repo, ".codeplane")
    return repo_to_dir


def match_gt_defs(gt_defs, conn):
    matched = []
    for rd in gt_defs:
        name = rd["name"]
        path = rd["path"]
        if "::" in name:
            name = name.split("::")[-1]
        if "." in name and not name.startswith("__"):
            name = name.split(".")[-1]
        rows = list(conn.execute(
            "SELECT d.def_uid, d.start_line FROM def_facts d "
            "JOIN files f ON d.file_id=f.id WHERE d.name=? AND f.path=?",
            (name, path),
        ))
        if rows:
            gt_line = rd.get("start_line", 0)
            best = min(rows, key=lambda r: abs(r[1] - gt_line))
            matched.append(best[0])
        else:
            rows = list(conn.execute(
                "SELECT d.def_uid, f.path FROM def_facts d "
                "JOIN files f ON d.file_id=f.id WHERE d.name=?",
                (name,),
            ))
            for r in rows:
                if r[1].endswith(path) or path.endswith(r[1]):
                    matched.append(r[0])
                    break
    return matched


def term_match_sql(terms, conn):
    found = set()
    for term in terms:
        if len(term) < 2:
            continue
        pat = f"%{term}%"
        for r in conn.execute(
            "SELECT def_uid FROM def_facts "
            "WHERE name LIKE ? COLLATE NOCASE "
            "OR qualified_name LIKE ? COLLATE NOCASE "
            "OR lexical_path LIKE ? COLLATE NOCASE",
            (pat, pat, pat),
        ):
            found.add(r[0])
    return found


def run():
    t_start = time.time()
    print("Loading ONNX model...", flush=True)
    from fastembed import TextEmbedding
    model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", max_length=512)
    print(f"  Done ({time.time()-t_start:.1f}s)", flush=True)

    repo_index = build_repo_index()

    # Pre-scan: count total work
    work_items = []  # (entry, codeplane_dir, gt_files)
    total_queries = 0
    for entry in sorted(os.listdir(DATA_DIR)):
        gt_dir = os.path.join(DATA_DIR, entry, "ground_truth")
        if not os.path.isdir(gt_dir):
            continue
        parts = entry.split("-", 1)
        if len(parts) < 2:
            continue
        info = repo_index.get(parts[1].lower())
        if not info:
            continue
        npz = os.path.join(info, "def_embedding", "def_embeddings.npz")
        if not os.path.isfile(npz):
            continue
        gt_files = [f for f in sorted(glob.glob(os.path.join(gt_dir, "*.json")))
                     if "non_ok" not in os.path.basename(f)]
        nq = 0
        for gf in gt_files:
            try:
                d = json.load(open(gf))
                nq += len(d.get("queries", []))
            except Exception:
                pass
        if nq:
            work_items.append((entry, info, gt_files))
            total_queries += nq

    print(f"\n{len(work_items)} repos, {total_queries} queries to process\n", flush=True)

    # Collect all (rank, sim, in_term, repo, n_defs) tuples
    all_data = []
    queries_done = 0

    for wi, (entry, cpdir, gt_files) in enumerate(work_items):
        db_path = os.path.join(cpdir, "index.db")
        npz_path = os.path.join(cpdir, "def_embedding", "def_embeddings.npz")

        data = np.load(npz_path, allow_pickle=False)
        matrix = data["matrix"]
        uids_arr = data["uids"]
        n_defs = len(uids_arr)
        uid_set_idx = {u: i for i, u in enumerate(uids_arr)}

        # Cast matrix to float32 once per repo (avoid per-query cast)
        mat32 = matrix.astype(np.float32)

        conn = sqlite3.connect(db_path)

        for gf in gt_files:
            try:
                d = json.load(open(gf))
            except Exception:
                continue
            gt_defs = d.get("minimum_sufficient_defs", [])
            queries = d.get("queries", [])
            if not gt_defs or not queries:
                continue

            gt_uids = match_gt_defs(gt_defs, conn)
            if not gt_uids:
                continue
            gt_uid_set = set(gt_uids)
            gt_in_emb = {u for u in gt_uid_set if u in uid_set_idx}

            for q in queries:
                qt = q.get("query_text", "")
                if not qt:
                    continue

                # Embedding: embed + matmul
                vecs = list(model.embed([qt], batch_size=1))
                q_vec = np.array(vecs[0], dtype=np.float32)
                norm = np.linalg.norm(q_vec)
                if norm > 0:
                    q_vec /= norm
                sims = mat32 @ q_vec
                ranked = np.argsort(sims)[::-1]

                # Find ranks of GT defs (early stop)
                rank_of = {}
                for rank, idx in enumerate(ranked):
                    u = uids_arr[idx]
                    if u in gt_in_emb:
                        rank_of[u] = rank
                    if len(rank_of) == len(gt_in_emb):
                        break

                # Term match
                parsed = parse_task(qt)
                terms = parsed.primary_terms + parsed.secondary_terms
                term_found = term_match_sql(terms, conn) if terms else set()

                for uid in gt_uid_set:
                    r = rank_of.get(uid)
                    s = float(sims[uid_set_idx[uid]]) if uid in uid_set_idx else None
                    in_t = uid in term_found
                    in_emb_matrix = uid in gt_in_emb
                    all_data.append((entry, r, s, in_t, in_emb_matrix, n_defs))

                queries_done += 1
                if queries_done % 200 == 0:
                    elapsed = time.time() - t_start
                    rate = queries_done / elapsed
                    eta = (total_queries - queries_done) / rate if rate > 0 else 0
                    print(f"  [{queries_done:5d}/{total_queries}] "
                          f"{100*queries_done/total_queries:5.1f}%  "
                          f"{rate:.0f} q/s  ETA {eta:.0f}s  "
                          f"repo {wi+1}/{len(work_items)} {entry}",
                          flush=True)

        conn.close()

    elapsed = time.time() - t_start
    print(f"\nDone: {queries_done} queries in {elapsed:.0f}s "
          f"({queries_done/elapsed:.1f} q/s)\n")

    # ── Analysis ──
    emb_data = [(repo, r, s, in_t, nd)
                for repo, r, s, in_t, in_emb, nd in all_data
                if in_emb and r is not None]
    all_pairs = [(repo, r, s, in_t, nd)
                 for repo, r, s, in_t, in_emb, nd in all_data]

    print(f"{'='*70}")
    print(f"RECALL ANALYSIS")
    print(f"  Total GT def×query pairs: {len(all_pairs)}")
    print(f"  With embedding coverage:  {len(emb_data)}")
    print(f"{'='*70}\n")

    ranks = [d[1] for d in emb_data]
    sims_ = [d[2] for d in emb_data]
    in_terms = [d[3] for d in emb_data]
    n_defs_list = [d[4] for d in emb_data]

    # 1. Rank distribution
    print("EMBEDDING RANK of GT-correct defs (lower = better):")
    for k in [10, 25, 50, 100, 200, 500, 1000, 5000]:
        c = sum(1 for r in ranks if r < k)
        print(f"  top-{k:>5d}: {c:5d}/{len(ranks)} ({100*c/len(ranks):5.1f}%)")
    print(f"  ALL      : {len(ranks):5d}/{len(ranks)} (100.0%)")

    # 2. Normalized rank
    print("\nNORMALIZED RANK (rank / total_defs):")
    norm_ranks = [r / nd for r, nd in zip(ranks, n_defs_list)]
    narr = np.array(norm_ranks)
    for p in [5, 10, 25, 50, 75, 90, 95, 99]:
        print(f"  p{p:2d}: {np.percentile(narr, p):.4f}  "
              f"(top {100*np.percentile(narr, p):.2f}% of defs)")

    # 3. Similarity distribution
    print("\nSIMILARITY of GT-correct defs:")
    sarr = np.array(sims_)
    for p in [0, 5, 10, 25, 50, 75, 90, 95, 100]:
        print(f"  p{p:>3d}: {np.percentile(sarr, p):.4f}")

    # 4. Term match recall
    term_hit = sum(1 for t in in_terms if t)
    print(f"\nTERM MATCH RECALL: {term_hit}/{len(in_terms)} "
          f"({100*term_hit/len(in_terms):.1f}%)")

    # 5. Unique recall by harvester at each top_k
    print(f"\nUNIQUE RECALL (embedding top_k vs term match):")
    print(f"{'top_k':>8s} | {'emb+term':>10s} | {'emb_only':>10s} | "
          f"{'term_only':>10s} | {'neither':>10s} | {'total_recall':>12s}")
    print("-" * 80)
    for top_k in [50, 100, 200, 500, 1000, 5000, 9999999]:
        label = "ALL" if top_k > 99999 else str(top_k)
        both = emb_only = term_only = neither = 0
        for r, s, in_t in zip(ranks, sims_, in_terms):
            in_emb = r < top_k
            if in_emb and in_t:
                both += 1
            elif in_emb and not in_t:
                emb_only += 1
            elif not in_emb and in_t:
                term_only += 1
            else:
                neither += 1
        total = len(ranks)
        found = both + emb_only + term_only
        print(f"{label:>8s} | {both:5d} ({100*both/total:4.1f}%) | "
              f"{emb_only:5d} ({100*emb_only/total:4.1f}%) | "
              f"{term_only:5d} ({100*term_only/total:4.1f}%) | "
              f"{neither:5d} ({100*neither/total:4.1f}%) | "
              f"{found:5d} ({100*found/total:4.1f}%)")

    # 6. Embedding-only examples
    emb_only_items = [(repo, r, s, nd)
                      for repo, r, s, in_t, nd in emb_data
                      if r < 200 and not in_t]
    if emb_only_items:
        print(f"\nSAMPLE: {len(emb_only_items)} GT defs found by embedding-only "
              f"(top-200, not term-matched):")
        emb_only_items.sort(key=lambda x: x[1])
        for repo, r, s, nd in emb_only_items[:15]:
            print(f"  rank={r:4d}/{nd} sim={s:.3f} repo={repo}")

    # 7. Per-repo breakdown
    print(f"\nPER-REPO RECALL (top_k=ALL, term match):")
    rg = defaultdict(list)
    for repo, r, s, in_t, nd in emb_data:
        rg[repo].append((r, s, in_t, nd))
    print(f"{'repo':>30s} | {'pairs':>5s} | {'term%':>6s} | {'emb_only%':>9s}")
    print("-" * 60)
    for repo in sorted(rg.keys()):
        items = rg[repo]
        n = len(items)
        tp = 100 * sum(1 for _, _, t, _ in items if t) / n
        eo = 100 * sum(1 for _, _, t, _ in items if not t) / n
        print(f"{repo:>30s} | {n:5d} | {tp:5.1f}% | {eo:8.1f}%")

    # Write raw data
    out = "/tmp/recall_data.json"
    with open(out, "w") as f:
        json.dump([{"repo": d[0], "rank": d[1], "sim": d[2],
                    "in_term": d[3], "n_defs": d[4]}
                   for d in emb_data], f)
    print(f"\nRaw data written to {out}")


if __name__ == "__main__":
    run()
