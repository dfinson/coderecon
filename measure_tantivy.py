#!/usr/bin/env python3
"""Measure whether Tantivy/BM25 adds signal beyond embedding + SQL term match.

Two questions:
  1. Does Tantivy find GT defs that embedding + term match miss?  (unique recall)
  2. Does BM25 score correlate with relevance?  (scoring signal)

Approach: open Tantivy indexes directly, run queries, get BM25 scores,
map file hits to def_uids, compare against embedding ranks and term match.

~20K queries across ~80 repos.
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
from codeplane.index._internal.indexing.lexical import LexicalIndex

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
            cpdir = os.path.join(cdir, repo, ".codeplane")
            db = os.path.join(cpdir, "index.db")
            tantivy = os.path.join(cpdir, "tantivy")
            if os.path.isfile(db) and os.path.isdir(tantivy):
                repo_to_dir[repo.lower()] = cpdir
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


def tantivy_to_def_uids(search_results, conn):
    """Map Tantivy file+line hits to def_uids."""
    # Group by file
    file_hits = defaultdict(list)
    for hit in search_results.results:
        file_hits[hit.file_path].append(hit.line)

    found = set()
    for fpath, lines in file_hits.items():
        row = conn.execute(
            "SELECT id FROM files WHERE path=?", (fpath,)
        ).fetchone()
        if not row:
            continue
        defs = conn.execute(
            "SELECT def_uid, start_line, end_line FROM def_facts WHERE file_id=?",
            (row[0],),
        ).fetchall()
        for line in lines:
            for uid, sl, el in defs:
                if sl <= line <= el:
                    found.add(uid)
                    break
    return found


def tantivy_bm25_for_defs(bm25_scores, conn):
    """Map file-level BM25 scores to def_uids. Each def gets its file's score."""
    def_scores = {}
    for fpath, score in bm25_scores.items():
        row = conn.execute("SELECT id FROM files WHERE path=?", (fpath,)).fetchone()
        if not row:
            continue
        defs = conn.execute(
            "SELECT def_uid FROM def_facts WHERE file_id=?", (row[0],)
        ).fetchall()
        for (uid,) in defs:
            if uid not in def_scores or score > def_scores[uid]:
                def_scores[uid] = score
    return def_scores


def run():
    t_start = time.time()

    repo_index = build_repo_index()

    # Pre-scan
    work_items = []
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

    print(f"{len(work_items)} repos, {total_queries} queries\n", flush=True)

    # Collect per-uid data:
    # (repo, emb_rank, emb_sim, in_term, in_tantivy_bool, bm25_score, n_defs)
    all_data = []
    queries_done = 0
    tantivy_errors = 0

    from fastembed import TextEmbedding
    print("Loading ONNX model...", flush=True)
    model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", max_length=512)
    print(f"  Done ({time.time()-t_start:.1f}s)\n", flush=True)

    for wi, (entry, cpdir, gt_files) in enumerate(work_items):
        db_path = os.path.join(cpdir, "index.db")
        npz_path = os.path.join(cpdir, "def_embedding", "def_embeddings.npz")
        tantivy_dir = os.path.join(cpdir, "tantivy")

        # Load embedding
        data = np.load(npz_path, allow_pickle=False)
        matrix = data["matrix"]
        uids_arr = data["uids"]
        n_defs = len(uids_arr)
        uid_set_idx = {u: i for i, u in enumerate(uids_arr)}
        mat32 = matrix.astype(np.float32)

        conn = sqlite3.connect(db_path)

        # Open Tantivy
        try:
            lex = LexicalIndex(tantivy_dir)
        except Exception as e:
            print(f"  SKIP {entry}: Tantivy error: {e}", flush=True)
            tantivy_errors += 1
            conn.close()
            continue

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

                # Embedding
                vecs = list(model.embed([qt], batch_size=1))
                q_vec = np.array(vecs[0], dtype=np.float32)
                norm = np.linalg.norm(q_vec)
                if norm > 0:
                    q_vec /= norm
                sims = mat32 @ q_vec
                ranked = np.argsort(sims)[::-1]

                rank_of = {}
                for rank, idx in enumerate(ranked):
                    u = uids_arr[idx]
                    if u in gt_in_emb:
                        rank_of[u] = rank
                    if len(rank_of) == len(gt_in_emb):
                        break

                # Term match (SQL LIKE)
                parsed = parse_task(qt)
                terms = parsed.primary_terms + parsed.secondary_terms
                term_found = term_match_sql(terms, conn) if terms else set()

                # Tantivy boolean (AND search)
                try:
                    query_str = " ".join(terms) if terms else qt
                    tantivy_results = lex.search(query_str, limit=5000)
                    tantivy_uids = tantivy_to_def_uids(tantivy_results, conn)
                except Exception:
                    tantivy_uids = set()

                # Tantivy BM25 scores (OR search)
                try:
                    bm25_scores = lex.score_files_bm25(qt)
                    bm25_def_scores = tantivy_bm25_for_defs(bm25_scores, conn)
                except Exception:
                    bm25_def_scores = {}

                for uid in gt_uid_set:
                    r = rank_of.get(uid)
                    s = float(sims[uid_set_idx[uid]]) if uid in uid_set_idx else None
                    in_t = uid in term_found
                    in_tantivy = uid in tantivy_uids
                    bm25 = bm25_def_scores.get(uid, 0.0)
                    in_emb_matrix = uid in gt_in_emb
                    all_data.append((entry, r, s, in_t, in_tantivy, bm25,
                                     in_emb_matrix, n_defs))

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
          f"({queries_done/elapsed:.1f} q/s)")
    if tantivy_errors:
        print(f"  Tantivy errors: {tantivy_errors} repos skipped")

    # ── Analysis ──
    emb_data = [(repo, r, s, in_t, in_tv, bm25, nd)
                for repo, r, s, in_t, in_tv, bm25, in_emb, nd in all_data
                if in_emb and r is not None]

    print(f"\n{'='*70}")
    print(f"TANTIVY/BM25 ANALYSIS")
    print(f"  Total GT def×query pairs (with embedding): {len(emb_data)}")
    print(f"{'='*70}\n")

    if not emb_data:
        print("No data!")
        return

    ranks = [d[1] for d in emb_data]
    sims_ = [d[2] for d in emb_data]
    in_terms = [d[3] for d in emb_data]
    in_tantivy = [d[4] for d in emb_data]
    bm25s = [d[5] for d in emb_data]

    # Q1: Tantivy boolean recall
    tv_hit = sum(1 for t in in_tantivy if t)
    tm_hit = sum(1 for t in in_terms if t)
    print(f"BOOLEAN RECALL:")
    print(f"  SQL term match:     {tm_hit:5d}/{len(emb_data)} ({100*tm_hit/len(emb_data):.1f}%)")
    print(f"  Tantivy AND search: {tv_hit:5d}/{len(emb_data)} ({100*tv_hit/len(emb_data):.1f}%)")

    # Unique recall
    tv_only = sum(1 for t, tm in zip(in_tantivy, in_terms) if t and not tm)
    tm_only = sum(1 for t, tm in zip(in_tantivy, in_terms) if tm and not t)
    both = sum(1 for t, tm in zip(in_tantivy, in_terms) if t and tm)
    neither = sum(1 for t, tm in zip(in_tantivy, in_terms) if not t and not tm)
    print(f"\n  Tantivy-only (not term match): {tv_only:5d} ({100*tv_only/len(emb_data):.1f}%)")
    print(f"  Term-only (not Tantivy):       {tm_only:5d} ({100*tm_only/len(emb_data):.1f}%)")
    print(f"  Both:                          {both:5d} ({100*both/len(emb_data):.1f}%)")
    print(f"  Neither:                       {neither:5d} ({100*neither/len(emb_data):.1f}%)")

    # Q1b: Does Tantivy find things that BOTH embedding + term match miss?
    print(f"\nTRIPLE HARVESTER COMPARISON (at embedding top_k):")
    print(f"{'top_k':>8s} | {'emb+tm+tv':>10s} | {'tv_unique':>10s} | "
          f"{'emb+tm':>10s} | {'unfound':>10s}")
    print("-" * 70)
    for top_k in [200, 500, 1000, 9999999]:
        label = "ALL" if top_k > 99999 else str(top_k)
        found_all = found_tv_unique = found_emb_tm = unfound = 0
        for r, s, in_t, in_tv in zip(ranks, sims_, in_terms, in_tantivy):
            in_emb = r < top_k
            found_by_emb_or_tm = in_emb or in_t
            if found_by_emb_or_tm and in_tv:
                found_all += 1
            elif in_tv and not found_by_emb_or_tm:
                found_tv_unique += 1
            elif found_by_emb_or_tm and not in_tv:
                found_emb_tm += 1
            else:
                unfound += 1
        total = len(ranks)
        print(f"{label:>8s} | {found_all:5d} ({100*found_all/total:4.1f}%) | "
              f"{found_tv_unique:5d} ({100*found_tv_unique/total:4.1f}%) | "
              f"{found_emb_tm:5d} ({100*found_emb_tm/total:4.1f}%) | "
              f"{unfound:5d} ({100*unfound/total:4.1f}%)")

    # Q2: BM25 score distribution for GT-correct vs random defs
    print(f"\nBM25 SCORE DISTRIBUTION:")
    bm25_arr = np.array(bm25s)
    has_bm25 = bm25_arr > 0
    print(f"  GT defs with BM25 > 0: {has_bm25.sum()}/{len(bm25_arr)} "
          f"({100*has_bm25.sum()/len(bm25_arr):.1f}%)")
    if has_bm25.sum() > 0:
        pos = bm25_arr[has_bm25]
        for p in [0, 10, 25, 50, 75, 90, 100]:
            print(f"    p{p:>3d}: {np.percentile(pos, p):.3f}")

    # Q2b: Correlation between BM25 and embedding sim
    print(f"\nCORRELATION (Spearman) among GT defs with BM25 > 0:")
    if has_bm25.sum() > 10:
        from scipy import stats
        mask = bm25_arr > 0
        r_bm25_sim, p_bm25_sim = stats.spearmanr(bm25_arr[mask],
                                                   np.array(sims_)[mask])
        r_bm25_rank, p_bm25_rank = stats.spearmanr(bm25_arr[mask],
                                                     np.array(ranks)[mask])
        print(f"  BM25 vs embedding_sim:  r={r_bm25_sim:.3f}  p={p_bm25_sim:.2e}")
        print(f"  BM25 vs embedding_rank: r={r_bm25_rank:.3f}  p={p_bm25_rank:.2e}")

    # Q3: Among GT defs NOT found by embedding top-200 or term match,
    # how many does Tantivy find?
    hard_cases = [(r, s, in_t, in_tv, bm25)
                  for r, s, in_t, in_tv, bm25
                  in zip(ranks, sims_, in_terms, in_tantivy, bm25s)
                  if r >= 200 and not in_t]
    if hard_cases:
        tv_saves = sum(1 for _, _, _, in_tv, _ in hard_cases if in_tv)
        print(f"\nHARD CASES (not in emb top-200, not in term match): {len(hard_cases)}")
        print(f"  Tantivy saves: {tv_saves}/{len(hard_cases)} "
              f"({100*tv_saves/len(hard_cases):.1f}%)")

    # Write raw data
    out = "/tmp/tantivy_recall_data.json"
    with open(out, "w") as f:
        json.dump([{"repo": d[0], "rank": d[1], "sim": d[2],
                    "in_term": d[3], "in_tantivy": d[4],
                    "bm25": d[5], "n_defs": d[6]}
                   for d in emb_data], f)
    print(f"\nRaw data written to {out}")


if __name__ == "__main__":
    run()
