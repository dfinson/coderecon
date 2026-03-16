#!/usr/bin/env python3
"""Ablation study: which scaffold components contribute most to embedding recall?

For each sample repo, builds 5 scaffold variants by incrementally adding components:
  V0: name only              (bare identifier)
  V1: name + signature       (+ parameter names)
  V2: V1 + docstring         (+ natural language purpose)
  V3: V2 + callees           (+ structural context)
  V4: V3 + decorators        (+ type/role context)
  V5: V4 + string literals   (+ string content from source)

Embeds all variants, runs GT queries, measures recall@k for each variant.
The marginal recall gain of each addition proves (or disproves) the priority order.

Runs on ~10 repos covering small/medium/large × multiple languages.
ETA: ~10 min per repo variant × 5 variants = ~50 min total, reduced by picking
repos with fewer defs.
"""

import json
import os
import re
import sqlite3
import sys
import glob
import time
from collections import defaultdict

import numpy as np

from codeplane.index._internal.indexing.file_embedding import (
    _compact_sig,
    _path_to_phrase,
    _word_split,
)


DATA_DIR = os.path.expanduser("~/.cpl-lab/data")
CLONE_BASE = os.path.expanduser("~/.cpl-lab/clones")
CLONE_SETS = ["cutoff", "eval", "ranker-gate"]

# Budget constants from production code
_DEF_DOC_BUDGET_CHARS = 120
_DEF_STRING_LIT_BUDGET_CHARS = 150
_DEF_STRING_LITS_MAX = 5

# Regex patterns for string literal extraction (from structural.py)
_STRING_REGEX_DQ = re.compile(r'"([^"]{4,80})"')
_STRING_REGEX_SQ = re.compile(r"'([^']{4,80})'")


def _extract_string_literals_regex(source_text, start_line, end_line):
    """Extract string literals from source text within a def span using regex.
    
    Replicates the logic from structural.py's _extract_string_literals_regex.
    start_line/end_line are 1-indexed (as stored in DB).
    """
    lines = source_text.split("\n")
    sl = max(0, start_line - 1)
    el = min(len(lines), end_line)
    source_slice = "\n".join(lines[sl:el])

    results = []
    total_chars = 0
    budget = _DEF_STRING_LIT_BUDGET_CHARS
    for match in _STRING_REGEX_DQ.finditer(source_slice):
        text = match.group(1)
        remaining = budget - total_chars
        if remaining <= 0:
            break
        results.append(text[:remaining])
        total_chars += len(text[:remaining])
    for match in _STRING_REGEX_SQ.finditer(source_slice):
        text = match.group(1)
        remaining = budget - total_chars
        if remaining <= 0:
            break
        results.append(text[:remaining])
        total_chars += len(text[:remaining])
    return results


def build_repo_index():
    """Returns dict: repo_name_lower -> (cpdir, clone_root)"""
    repo_to_dir = {}
    for cset in CLONE_SETS:
        cdir = os.path.join(CLONE_BASE, cset)
        if not os.path.isdir(cdir):
            continue
        for repo in os.listdir(cdir):
            clone_root = os.path.join(cdir, repo)
            cpdir = os.path.join(clone_root, ".codeplane")
            db = os.path.join(cpdir, "index.db")
            npz = os.path.join(cpdir, "def_embedding", "def_embeddings.npz")
            if os.path.isfile(db) and os.path.isfile(npz):
                repo_to_dir[repo.lower()] = (cpdir, clone_root)
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


def build_scaffold_variants(conn, clone_root):
    """Build 6 scaffold variants for every def in the repo.
    
    Returns: dict[def_uid] -> [v0, v1, v2, v3, v4, v5] scaffold strings
    """
    # Load all defs
    defs = conn.execute("""
        SELECT d.def_uid, d.name, d.kind, d.signature_text, d.docstring,
               d.decorators_json, d.qualified_name, d.lexical_path,
               d.file_id, d.start_line, d.end_line,
               f.path
        FROM def_facts d
        JOIN files f ON d.file_id = f.id
    """).fetchall()

    # Pre-load callees per def (by line overlap in ref_facts)
    # Batch: group refs by file_id
    all_refs = conn.execute("""
        SELECT file_id, start_line, token_text FROM ref_facts
    """).fetchall()
    
    # Build interval lookup: for each file, sorted list of (line, token)
    file_refs = defaultdict(list)
    for fid, line, token in all_refs:
        file_refs[fid].append((line, token))

    # Pre-load source files for string literal extraction
    file_id_to_path = {}
    for row in conn.execute("SELECT id, path FROM files").fetchall():
        file_id_to_path[row[0]] = row[1]
    
    source_cache = {}  # file_id -> source text or None
    
    def get_source(fid):
        if fid not in source_cache:
            fpath = file_id_to_path.get(fid)
            if fpath:
                full = os.path.join(clone_root, fpath)
                try:
                    with open(full, "r", errors="replace") as f:
                        source_cache[fid] = f.read()
                except (OSError, IOError):
                    source_cache[fid] = None
            else:
                source_cache[fid] = None
        return source_cache[fid]
    
    result = {}
    for row in defs:
        uid, name, kind, sig, doc, dec_json, qn, lp, fid, sl, el, fpath = row
        if not name:
            continue

        # ── V0: name only ──
        path_phrase = _path_to_phrase(fpath)
        v0_lines = []
        if path_phrase:
            v0_lines.append(f"module {path_phrase}")
        v0_lines.append(f"{kind} {name}")
        v0 = "DEF_SCAFFOLD\n" + "\n".join(v0_lines)

        # ── V1: name + signature ──
        v1_lines = list(v0_lines)
        if sig:
            compact = _compact_sig(name, sig)
            v1_lines[-1] = f"{kind} {compact}"
        # Add parent context
        qualified = qn or lp or ""
        if qualified and "." in qualified:
            parent = qualified.rsplit(".", 1)[0]
            parent_words = " ".join(_word_split(parent))
            if parent_words:
                v1_lines.append(f"in {parent_words}")
        v1 = "DEF_SCAFFOLD\n" + "\n".join(v1_lines)

        # ── V2: V1 + docstring ──
        v2_lines = list(v1_lines)
        if doc and len(doc.strip()) > 15:
            doc_clean = doc.strip()
            first_sentence = (doc_clean.split(".")[0].strip()
                              if "." in doc_clean
                              else doc_clean[:_DEF_DOC_BUDGET_CHARS])
            if first_sentence:
                v2_lines.append(f"describes {first_sentence[:_DEF_DOC_BUDGET_CHARS]}")
        v2 = "DEF_SCAFFOLD\n" + "\n".join(v2_lines)

        # ── V3: V2 + callees ──
        v3_lines = list(v2_lines)
        # Get callees from ref_facts by line overlap
        callees = set()
        for line, token in file_refs.get(fid, []):
            if sl <= line <= el and token and len(token) >= 2:
                callees.add(token)
        if callees:
            sorted_calls = sorted(callees)[:10]
            v3_lines.append(f"calls {', '.join(sorted_calls)}")
        v3 = "DEF_SCAFFOLD\n" + "\n".join(v3_lines)

        # ── V4: V3 + decorators ──
        v4_lines = list(v3_lines)
        if dec_json and dec_json != "[]":
            try:
                decs = []
                for dec_str in json.loads(dec_json):
                    name_str = dec_str.lstrip("@").split("(")[0].strip()
                    if name_str and len(name_str) >= 2:
                        decs.append(name_str)
                if decs:
                    v4_lines.append(f"decorated {', '.join(decs[:5])}")
            except (json.JSONDecodeError, TypeError):
                pass
        v4 = "DEF_SCAFFOLD\n" + "\n".join(v4_lines)

        # ── V5: V4 + string literals ──
        v5_lines = list(v4_lines)
        source = get_source(fid)
        if source:
            lits = _extract_string_literals_regex(source, sl, el)
            if lits:
                unique_lits = list(dict.fromkeys(lits))[:_DEF_STRING_LITS_MAX]
                v5_lines.append(f"strings {', '.join(unique_lits)}")
        v5 = "DEF_SCAFFOLD\n" + "\n".join(v5_lines)

        result[uid] = [v0, v1, v2, v3, v4, v5]

    return result


def run():
    t_start = time.time()
    
    print("Loading ONNX model...", flush=True)
    from fastembed import TextEmbedding
    model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", max_length=512)
    print(f"  Done ({time.time()-t_start:.1f}s)\n", flush=True)

    repo_index = build_repo_index()

    # Select repos for ablation: diverse sizes/languages, must have GT data
    # Pick repos with manageable def counts (< 15K) to keep runtime reasonable
    candidates = []
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
        cpdir_cand, clone_root_cand = info
        npz = os.path.join(cpdir_cand, "def_embedding", "def_embeddings.npz")
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
            data = np.load(npz, allow_pickle=False)
            n_defs = len(data["uids"])
            candidates.append((entry, cpdir_cand, clone_root_cand, gt_files, nq, n_defs))

    # Sort by n_defs, pick diverse set across languages
    candidates.sort(key=lambda x: x[5])
    
    # Pick one per language where possible, spread of sizes
    selected = []
    seen_langs = set()
    # Small repos first
    for entry, cpd, cr, gt_files, nq, nd in candidates:
        lang = entry.split("-")[0]
        if lang not in seen_langs and nd < 5000:
            seen_langs.add(lang)
            selected.append((entry, cpd, cr, gt_files, nq, nd))
    # Medium repos
    for entry, cpd, cr, gt_files, nq, nd in candidates:
        lang = entry.split("-")[0]
        if lang not in seen_langs and nd < 20000:
            seen_langs.add(lang)
            selected.append((entry, cpd, cr, gt_files, nq, nd))
    # Fill up to 12 if needed
    for entry, cpd, cr, gt_files, nq, nd in candidates:
        if len(selected) >= 12:
            break
        if (entry, cpd, cr, gt_files, nq, nd) not in selected and nd < 20000:
            selected.append((entry, cpd, cr, gt_files, nq, nd))
    
    N_VARIANTS = 6
    total_defs = sum(nd for _, _, _, _, _, nd in selected)
    total_queries = sum(nq for _, _, _, _, nq, _ in selected)
    print(f"Selected {len(selected)} repos:")
    for entry, _, _, _, nq, nd in selected:
        print(f"  {entry:>30s}: {nd:>6d} defs, {nq:>4d} queries")
    print(f"  TOTAL: {total_defs} defs × {N_VARIANTS} variants = {total_defs*N_VARIANTS} embeddings, "
          f"{total_queries} queries")
    print(f"  ETA: ~{total_defs*N_VARIANTS*0.3/1000:.0f} min for embedding + "
          f"~{total_queries*N_VARIANTS*15/1000/60:.0f} min for queries\n", flush=True)

    VARIANT_NAMES = [
        "V0: name only",
        "V1: + signature",
        "V2: + docstring",
        "V3: + callees",
        "V4: + decorators",
        "V5: + strings",
    ]

    # Per-variant recall accumulators
    # variant -> list of (rank, n_defs) for GT defs
    variant_ranks = [[] for _ in range(N_VARIANTS)]
    
    # Per-variant per-repo
    repo_variant_recall = defaultdict(lambda: [[] for _ in range(N_VARIANTS)])

    for ri, (entry, cpdir, clone_root, gt_files, nq, nd) in enumerate(selected):
        t_repo = time.time()
        db_path = os.path.join(cpdir, "index.db")
        conn = sqlite3.connect(db_path)
        
        print(f"\n[{ri+1}/{len(selected)}] {entry} ({nd} defs, {nq} queries)", flush=True)

        # Build scaffold variants
        print(f"  Building scaffolds...", flush=True)
        scaffolds = build_scaffold_variants(conn, clone_root)
        
        # Show sample scaffold
        sample_uid = next(iter(scaffolds))
        print(f"  Sample scaffold V5 ({len(scaffolds[sample_uid][5])} chars):")
        for line in scaffolds[sample_uid][5].split("\n")[:8]:
            print(f"    {line}")

        # Embed all variants: 5 passes
        uid_order = sorted(scaffolds.keys())
        uid_to_idx = {u: i for i, u in enumerate(uid_order)}
        
        variant_matrices = []
        for vi in range(N_VARIANTS):
            texts = [scaffolds[uid][vi] for uid in uid_order]
            print(f"  Embedding V{vi} ({len(texts)} texts)...", end="", flush=True)
            t0 = time.time()
            
            # Embed in batches
            all_vecs = []
            batch_size = 64  # small scaffolds, can use large batches
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i+batch_size]
                vecs = list(model.embed(batch, batch_size=len(batch)))
                all_vecs.extend(vecs)
            
            mat = np.array(all_vecs, dtype=np.float32)
            # Normalize rows
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1
            mat /= norms
            variant_matrices.append(mat)
            
            print(f" {time.time()-t0:.1f}s", flush=True)
        
        # Run GT queries against each variant
        print(f"  Running queries...", flush=True)
        q_count = 0
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
            # Filter to uids that exist in our scaffolds
            gt_in_scaffolds = {u for u in gt_uid_set if u in uid_to_idx}
            if not gt_in_scaffolds:
                continue
            
            for q in queries:
                qt = q.get("query_text", "")
                if not qt:
                    continue
                
                # Embed query
                vecs = list(model.embed([qt], batch_size=1))
                q_vec = np.array(vecs[0], dtype=np.float32)
                norm = np.linalg.norm(q_vec)
                if norm > 0:
                    q_vec /= norm
                
                # Score against each variant matrix
                for vi in range(N_VARIANTS):
                    sims = variant_matrices[vi] @ q_vec
                    ranked = np.argsort(sims)[::-1]
                    
                    for gt_uid in gt_in_scaffolds:
                        idx = uid_to_idx[gt_uid]
                        # Find this uid's rank
                        rank = int(np.where(ranked == idx)[0][0])
                        variant_ranks[vi].append((rank, len(uid_order)))
                        repo_variant_recall[entry][vi].append((rank, len(uid_order)))
                
                q_count += 1
                if q_count % 100 == 0:
                    elapsed = time.time() - t_repo
                    print(f"    {q_count}/{nq} queries ({elapsed:.0f}s)", flush=True)
        
        conn.close()
        print(f"  Done: {q_count} queries in {time.time()-t_repo:.0f}s", flush=True)

    # ── Analysis ──
    elapsed = time.time() - t_start
    print(f"\n{'='*70}")
    print(f"SCAFFOLD ABLATION RESULTS ({elapsed:.0f}s total)")
    print(f"{'='*70}\n")

    # Recall@k for each variant
    print(f"RECALL@k BY SCAFFOLD VARIANT:")
    print(f"{'':>20s}", end="")
    for k in [10, 25, 50, 100, 200, 500]:
        print(f" | {'top-'+str(k):>8s}", end="")
    print(f" |  n_pairs")
    print("-" * 95)
    
    for vi in range(N_VARIANTS):
        ranks = variant_ranks[vi]
        n = len(ranks)
        if n == 0:
            continue
        print(f"{VARIANT_NAMES[vi]:>20s}", end="")
        for k in [10, 25, 50, 100, 200, 500]:
            count = sum(1 for r, _ in ranks if r < k)
            print(f" | {100*count/n:7.1f}%", end="")
        print(f" | {n:7d}")

    # Marginal gain
    print(f"\nMARGINAL RECALL GAIN (each component's contribution):")
    print(f"{'':>20s}", end="")
    for k in [10, 25, 50, 100, 200, 500]:
        print(f" | {'top-'+str(k):>8s}", end="")
    print()
    print("-" * 85)
    
    for vi in range(1, N_VARIANTS):
        prev_ranks = variant_ranks[vi-1]
        curr_ranks = variant_ranks[vi]
        n = len(curr_ranks)
        if n == 0:
            continue
        label = ["+ signature", "+ docstring", "+ callees", "+ decorators", "+ strings"][vi-1]
        print(f"{label:>20s}", end="")
        for k in [10, 25, 50, 100, 200, 500]:
            prev_count = sum(1 for r, _ in prev_ranks if r < k)
            curr_count = sum(1 for r, _ in curr_ranks if r < k)
            delta = 100 * (curr_count - prev_count) / n
            print(f" | {delta:>+7.1f}%", end="")
        print()

    # Normalized rank by variant
    print(f"\nNORMALIZED RANK (rank/total_defs) PERCENTILES:")
    print(f"{'':>20s} |   p25 |   p50 |   p75 |   p90 |   p95")
    print("-" * 70)
    for vi in range(N_VARIANTS):
        ranks = variant_ranks[vi]
        if not ranks:
            continue
        norms = [r/nd for r, nd in ranks]
        arr = np.array(norms)
        print(f"{VARIANT_NAMES[vi]:>20s}", end="")
        for p in [25, 50, 75, 90, 95]:
            print(f" | {np.percentile(arr, p):5.3f}", end="")
        print()

    # Per-repo breakdown at top-100
    print(f"\nPER-REPO RECALL@100 BY VARIANT:")
    print(f"{'repo':>30s} | {'V0':>6s} | {'V1':>6s} | {'V2':>6s} | {'V3':>6s} | {'V4':>6s} | {'V5':>6s} | {'V2-V1':>6s} | {'V3-V2':>6s} | {'V4-V3':>6s} | {'V5-V4':>6s}")
    print("-" * 130)
    for repo in sorted(repo_variant_recall.keys()):
        vals = repo_variant_recall[repo]
        n = len(vals[0])
        if n == 0:
            continue
        pcts = []
        for vi in range(N_VARIANTS):
            c = sum(1 for r, _ in vals[vi] if r < 100)
            pcts.append(100*c/n)
        print(f"{repo:>30s}", end="")
        for p in pcts:
            print(f" | {p:5.1f}%", end="")
        # Deltas
        for vi in [2, 3, 4, 5]:
            d = pcts[vi] - pcts[vi-1]
            print(f" | {d:>+5.1f}%", end="")
        print()

    # Component availability stats
    print(f"\nCOMPONENT AVAILABILITY ACROSS ALL REPOS:")
    # We'll count from the scaffolds we built
    print("  (Would need to re-scan - skipped for now)")
    
    # Write raw data
    out = "/tmp/scaffold_ablation.json"
    export = {}
    for vi in range(N_VARIANTS):
        export[VARIANT_NAMES[vi]] = [{"rank": r, "n_defs": nd} 
                                      for r, nd in variant_ranks[vi]]
    with open(out, "w") as f:
        json.dump(export, f)
    print(f"\nRaw data written to {out}")


if __name__ == "__main__":
    run()
