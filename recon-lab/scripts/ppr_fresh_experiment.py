"""PPR+SPLADE Experiment — built fresh from index.db graphs + JSONL ground truth.

NO stale parquet data. Uses:
  - index.db ref_facts/import_facts/interface_impl_facts → graph edges
  - index.db splade_vecs → sparse learned retrieval vectors
  - queries.jsonl seeds/pins → PPR personalization + SPLADE query proxy
  - touched_objects.jsonl → ground truth labels

Compares: 1-hop, PPR, SPLADE (seed-centroid), PPR+SPLADE fusion.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from scipy import sparse

SPLADE_VOCAB_SIZE = 30522  # WordPiece vocab for splade-mini

# ─── Configuration ───────────────────────────────────────────────────────────

DATA_ROOT = Path(os.path.expanduser("~/.recon/recon-lab/data"))
CLONES_ROOT = Path(os.path.expanduser("~/.recon/recon-lab/clones"))

# Maps: index.db path → GT directory prefix pattern
REPO_MAP: dict[str, dict[str, str]] = {
    "eval/AutoMapper": {"gt_prefix": "csharp-automapper", "index": "eval/AutoMapper"},
    "eval/Catch2": {"gt_prefix": "cpp-catch2", "index": "eval/Catch2"},
    "eval/RxSwift": {"gt_prefix": "swift-rxswift", "index": "eval/RxSwift"},
    "eval/axum": {"gt_prefix": "rust-axum", "index": "eval/axum"},
    "eval/celery": {"gt_prefix": "python-celery", "index": "eval/celery"},
    "eval/diesel": {"gt_prefix": "rust-diesel", "index": "eval/diesel"},
    "eval/esbuild": {"gt_prefix": "typescript-esbuild", "index": "eval/esbuild"},
    "eval/fiber": {"gt_prefix": "go-fiber", "index": "eval/fiber"},
    "eval/gin": {"gt_prefix": "go-gin", "index": "eval/gin"},
    "eval/grpc": {"gt_prefix": "cpp-grpc", "index": "eval/grpc"},
    "eval/lombok": {"gt_prefix": "java-lombok", "index": "eval/lombok"},
    "eval/mockito": {"gt_prefix": "java-mockito", "index": "eval/mockito"},
    "eval/pydantic": {"gt_prefix": "python-pydantic", "index": "eval/pydantic"},
    "eval/sinatra": {"gt_prefix": "ruby-sinatra", "index": "eval/sinatra"},
    "eval/swift-composable-architecture": {
        "gt_prefix": "swift-composable-architecture",
        "index": "eval/swift-composable-architecture",
    },
    "eval/vitest": {"gt_prefix": "typescript-vitest", "index": "eval/vitest"},
    "eval/whoops": {"gt_prefix": "php-whoops", "index": "eval/whoops"},
    "eval/xunit": {"gt_prefix": "csharp-xunit", "index": "eval/xunit"},
    "ranker-gate/Alamofire": {"gt_prefix": "swift-alamofire", "index": "ranker-gate/Alamofire"},
}


# ─── Graph Loading ───────────────────────────────────────────────────────────


def decode_splade_blob(blob: bytes) -> dict[int, float]:
    """Decode int32+float32 packed sparse SPLADE vector."""
    result = {}
    for i in range(0, len(blob), 8):
        tid = struct.unpack('<I', blob[i:i+4])[0]
        weight = struct.unpack('<f', blob[i+4:i+8])[0]
        result[tid] = weight
    return result


def load_repo_graph(index_path: Path) -> dict[str, Any]:
    """Load graph + SPLADE vectors from a repo's index.db.

    Returns:
        {
            "nodes": {def_uid: {"path": str, "kind": str, "name": str, "start_line": int}},
            "edges": list[(src_uid, dst_uid)],  # directed: src references dst
            "path_to_uids": {path: [def_uids...]},
            "name_to_uids": {name: [def_uids...]},
            "candidate_key_to_uid": {"path:kind:name:line": def_uid},
            "splade_vecs": {def_uid: {token_id: weight, ...}},
        }
    """
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Load files for path resolution
    cursor.execute("SELECT id, path FROM files")
    file_id_to_path = {row["id"]: row["path"] for row in cursor.fetchall()}

    # Load all defs
    cursor.execute(
        "SELECT def_uid, file_id, kind, name, start_line, end_line FROM def_facts"
    )
    nodes: dict[str, dict] = {}
    path_to_uids: dict[str, list[str]] = defaultdict(list)
    name_to_uids: dict[str, list[str]] = defaultdict(list)
    candidate_key_to_uid: dict[str, str] = {}

    for row in cursor.fetchall():
        uid = row["def_uid"]
        path = file_id_to_path.get(row["file_id"], "")
        info = {
            "path": path,
            "kind": row["kind"],
            "name": row["name"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
        }
        nodes[uid] = info
        path_to_uids[path].append(uid)
        if row["name"]:
            name_to_uids[row["name"]].append(uid)
        # candidate_key format: path:kind:name:start_line
        ckey = f"{path}:{row['kind']}:{row['name']}:{row['start_line']}"
        candidate_key_to_uid[ckey] = uid

    # Load edges from ref_facts (cross-file references)
    # Edge: the scope containing the reference → the target def
    # We approximate: file_id of ref → target_def_uid
    # For PPR we need def-to-def edges. Use: defs in ref's file → target_def
    # More precise: use scope_id to find the enclosing def
    edges: list[tuple[str, str]] = []

    # Strategy: for each reference, find which def contains it (by file+line range)
    # This is expensive for large repos. Simpler approximation:
    # Group refs by file, connect all defs in that file to the target.
    # Even simpler: file-level edges (all defs in src_file ↔ target_def)
    # Best balance: use ref's file_id+start_line to find enclosing def via scope

    # Actually the simplest meaningful approach: connect ref's file defs to target
    # But that's too dense. Let's use a moderate approach:
    # For each cross-file reference, add edge: closest_def_in_file → target_def

    # Load ref_facts with cross-file references
    cursor.execute("""
        SELECT r.file_id, r.start_line, r.target_def_uid
        FROM ref_facts r
        WHERE r.role = 'reference'
          AND r.target_def_uid IS NOT NULL
    """)

    # Build file_id → sorted list of (start_line, end_line, def_uid) for scope resolution
    path_to_file_id = {path: fid for fid, path in file_id_to_path.items()}
    file_defs: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    for uid, info in nodes.items():
        fid = path_to_file_id.get(info["path"])
        if fid is not None:
            file_defs[fid].append((info["start_line"], info.get("end_line") or 999999, uid))

    # Sort by start_line for binary search
    for fid in file_defs:
        file_defs[fid].sort()

    # Process refs - find enclosing def for each ref
    seen_edges: set[tuple[str, str]] = set()
    for row in cursor.fetchall():
        target_uid = row["target_def_uid"]
        if target_uid not in nodes:
            continue
        ref_file_id = row["file_id"]
        ref_line = row["start_line"]

        # Find enclosing def: largest start_line <= ref_line where end_line >= ref_line
        defs_in_file = file_defs.get(ref_file_id, [])
        enclosing_uid = None
        for start, end, uid in reversed(defs_in_file):
            if start <= ref_line <= end:
                enclosing_uid = uid
                break
            if start < ref_line:
                # Fallback: closest def before the ref
                enclosing_uid = uid
                break

        if enclosing_uid and enclosing_uid != target_uid:
            edge = (enclosing_uid, target_uid)
            if edge not in seen_edges:
                seen_edges.add(edge)
                edges.append(edge)

    # Also add interface_impl edges (bidirectional)
    cursor.execute(
        "SELECT implementor_def_uid, interface_def_uid FROM interface_impl_facts"
    )
    for row in cursor.fetchall():
        impl_uid = row["implementor_def_uid"]
        iface_uid = row["interface_def_uid"]
        if impl_uid in nodes and iface_uid in nodes:
            for edge in [(impl_uid, iface_uid), (iface_uid, impl_uid)]:
                if edge not in seen_edges:
                    seen_edges.add(edge)
                    edges.append(edge)

    # Load SPLADE vectors
    splade_vecs: dict[str, dict[int, float]] = {}
    cursor.execute("SELECT def_uid, vector_blob FROM splade_vecs")
    for row in cursor.fetchall():
        uid = row["def_uid"]
        blob = row["vector_blob"]
        if blob and uid in nodes:
            splade_vecs[uid] = decode_splade_blob(blob)

    conn.close()

    return {
        "nodes": nodes,
        "edges": edges,
        "path_to_uids": dict(path_to_uids),
        "name_to_uids": dict(name_to_uids),
        "candidate_key_to_uid": candidate_key_to_uid,
        "splade_vecs": splade_vecs,
    }


# ─── PPR ─────────────────────────────────────────────────────────────────────


def personalized_pagerank(
    adj: sparse.csr_matrix,
    seed_indices: np.ndarray,
    alpha: float = 0.15,
    max_iter: int = 50,
    tol: float = 1e-6,
) -> np.ndarray:
    """Power-iteration PPR. Returns score vector over all nodes."""
    n = adj.shape[0]
    if n == 0 or len(seed_indices) == 0:
        return np.zeros(n, dtype=np.float64)

    # Personalization vector (uniform over seeds)
    p = np.zeros(n, dtype=np.float64)
    p[seed_indices] = 1.0 / len(seed_indices)

    # Row-normalize adjacency for transition matrix
    row_sums = np.array(adj.sum(axis=1)).flatten()
    row_sums[row_sums == 0] = 1.0
    T = sparse.diags(1.0 / row_sums) @ adj

    # Power iteration
    x = p.copy()
    for _ in range(max_iter):
        x_new = (1 - alpha) * (T.T @ x) + alpha * p
        if np.abs(x_new - x).sum() < tol:
            break
        x = x_new

    return x


# ─── Seed Resolution ─────────────────────────────────────────────────────────


def resolve_seeds(
    query: dict,
    graph: dict[str, Any],
) -> set[str]:
    """Resolve query seeds/pins to def_uids in the graph."""
    resolved: set[str] = set()
    name_to_uids = graph["name_to_uids"]
    path_to_uids = graph["path_to_uids"]

    # Resolve seeds by name
    for seed_name in query.get("seeds", []):
        # Try exact match
        if seed_name in name_to_uids:
            resolved.update(name_to_uids[seed_name])
        # Try dotted path (e.g. "pydantic.PrivateAttr" → "PrivateAttr")
        if "." in seed_name:
            short = seed_name.rsplit(".", 1)[-1]
            if short in name_to_uids:
                resolved.update(name_to_uids[short])

    # Resolve pins by path → all defs in that file
    for pin_path in query.get("pins", []):
        if pin_path in path_to_uids:
            resolved.update(path_to_uids[pin_path])

    return resolved


# ─── Ground Truth Resolution ─────────────────────────────────────────────────


def resolve_gt(
    touched_objects: list[dict],
    graph: dict[str, Any],
) -> set[str]:
    """Resolve GT touched_objects to def_uids in the graph.

    Matching strategy:
      1. Exact candidate_key match (path:kind:name:start_line)
      2. Fuzzy: same path + name (line numbers may shift between PR and main)
    """
    resolved: set[str] = set()
    ck_map = graph["candidate_key_to_uid"]
    nodes = graph["nodes"]

    for obj in touched_objects:
        ckey = f"{obj['path']}:{obj['kind']}:{obj['name']}:{obj['start_line']}"
        if ckey in ck_map:
            resolved.add(ck_map[ckey])
            continue

        # Fuzzy: same path + name (handles line drift between main and PR branch)
        for uid, info in nodes.items():
            if info["path"] == obj["path"] and info["name"] == obj["name"] and info["kind"] == obj["kind"]:
                resolved.add(uid)
                break

    return resolved


# ─── Evaluation ──────────────────────────────────────────────────────────────


def build_splade_matrix(
    uid_list: list[str],
    splade_vecs: dict[str, dict[int, float]],
) -> sparse.csr_matrix:
    """Build sparse (n_nodes × vocab) matrix from SPLADE vectors."""
    rows, cols, data = [], [], []
    for i, uid in enumerate(uid_list):
        vec = splade_vecs.get(uid)
        if vec:
            for tid, w in vec.items():
                rows.append(i)
                cols.append(tid)
                data.append(w)
    return sparse.csr_matrix(
        (np.array(data, dtype=np.float32), (rows, cols)),
        shape=(len(uid_list), SPLADE_VOCAB_SIZE),
    )


def compute_splade_scores(
    seed_indices: np.ndarray,
    splade_matrix: sparse.csr_matrix,
) -> np.ndarray:
    """Compute SPLADE similarity: seed-centroid dot product against all nodes.

    Uses prebuilt sparse matrix for fast matmul.
    """
    n = splade_matrix.shape[0]
    if len(seed_indices) == 0:
        return np.zeros(n, dtype=np.float64)

    # Centroid = mean of seed rows
    seed_rows = splade_matrix[seed_indices]
    centroid = seed_rows.mean(axis=0)  # (1, vocab)

    # Dot product: all_nodes × centroid.T → (n, 1)
    scores = splade_matrix.dot(centroid.T).A.flatten()
    return scores.astype(np.float64)


def normalize_scores(scores: np.ndarray) -> np.ndarray:
    """Min-max normalize to [0, 1]."""
    mn, mx = scores.min(), scores.max()
    if mx - mn < 1e-12:
        return np.zeros_like(scores)
    return (scores - mn) / (mx - mn)


def evaluate_query(
    graph: dict[str, Any],
    seed_uids: set[str],
    gt_uids: set[str],
    uid_to_idx: dict[str, int],
    uid_list: list[str],
    adj: sparse.csr_matrix,
    splade_matrix: sparse.csr_matrix,
) -> dict[str, float] | None:
    """Run PPR, SPLADE, fusion, and 1-hop for a single query."""
    if not seed_uids or not gt_uids:
        return None

    n = adj.shape[0]
    seed_indices = np.array([uid_to_idx[u] for u in seed_uids if u in uid_to_idx])
    gt_indices = set(uid_to_idx[u] for u in gt_uids if u in uid_to_idx)

    if len(seed_indices) == 0 or len(gt_indices) == 0:
        return None

    # Remove seeds from GT (we're measuring discovery of NEW items)
    gt_indices -= set(seed_indices.tolist())
    if not gt_indices:
        return None

    # --- PPR ---
    ppr_scores = personalized_pagerank(adj, seed_indices)

    # --- SPLADE (seed-centroid similarity) ---
    splade_scores = compute_splade_scores(seed_indices, splade_matrix)

    # --- Fusion: normalized PPR + normalized SPLADE ---
    ppr_norm = normalize_scores(ppr_scores)
    splade_norm = normalize_scores(splade_scores)
    fusion_scores = 0.5 * ppr_norm + 0.5 * splade_norm

    # --- 1-hop baseline: binary indicator for direct neighbors of seeds ---
    hop1 = np.zeros(n, dtype=np.float64)
    for si in seed_indices:
        neighbors = adj[si].indices
        hop1[neighbors] = 1.0

    # Zero out seeds themselves
    hop1[seed_indices] = 0.0
    ppr_scores[seed_indices] = 0.0
    splade_scores[seed_indices] = 0.0
    fusion_scores[seed_indices] = 0.0

    # --- Metrics ---
    all_indices = set(range(n)) - set(seed_indices.tolist())
    if not all_indices:
        return None

    all_arr = np.array(sorted(all_indices))

    results = {}
    methods = [
        ("hop1", hop1),
        ("ppr", ppr_scores),
        ("splade", splade_scores),
        ("fusion", fusion_scores),
    ]

    for method_name, scores in methods:
        # Rank by score (descending)
        ranked = np.argsort(-scores[all_arr])
        ranked_global = all_arr[ranked]

        # Recall@K
        for k in [10, 20, 50, 100]:
            top_k = set(ranked_global[:k].tolist())
            recall = len(top_k & gt_indices) / max(len(gt_indices), 1)
            results[f"{method_name}_recall@{k}"] = recall

        # Mean Reciprocal Rank
        mrr = 0.0
        for rank, idx in enumerate(ranked_global, 1):
            if idx in gt_indices:
                mrr = 1.0 / rank
                break
        results[f"{method_name}_mrr"] = mrr

    # Discovery analysis
    hop1_found = set(np.where(hop1 > 0)[0]) & gt_indices
    ppr_nonzero = set(np.where(ppr_scores > 0)[0]) & gt_indices
    splade_nonzero = set(np.where(splade_scores > 0)[0]) & gt_indices
    fusion_nonzero = set(np.where(fusion_scores > 0)[0]) & gt_indices

    results["gt_total"] = len(gt_indices)
    results["hop1_gt_found"] = len(hop1_found)
    results["ppr_gt_found"] = len(ppr_nonzero)
    results["splade_gt_found"] = len(splade_nonzero)
    results["fusion_gt_found"] = len(fusion_nonzero)
    results["ppr_extra_discovery"] = len(ppr_nonzero - hop1_found)
    results["fusion_extra_over_ppr"] = len(fusion_nonzero - ppr_nonzero)

    return results


# ─── Main Pipeline ───────────────────────────────────────────────────────────


def run_experiment(max_repos: int | None = None, max_queries_per_repo: int | None = None):
    print("=" * 70)
    print("PPR FRESH EXPERIMENT: From index.db graphs + JSONL ground truth")
    print("=" * 70)
    print()

    all_results: list[dict[str, float]] = []
    repo_stats: list[dict] = []

    repo_items = list(REPO_MAP.items())
    if max_repos:
        repo_items = repo_items[:max_repos]

    for repo_key, repo_info in repo_items:
        gt_prefix = repo_info["gt_prefix"]
        index_rel = repo_info["index"]
        index_path = CLONES_ROOT / index_rel / ".recon" / "index.db"

        if not index_path.exists():
            print(f"  SKIP {repo_key}: no index.db")
            continue

        # Find GT worktree directories for this repo
        gt_dirs = sorted([
            d for d in DATA_ROOT.iterdir()
            if d.is_dir() and d.name.startswith(gt_prefix + "_pr")
        ])

        if not gt_dirs:
            print(f"  SKIP {repo_key}: no GT worktrees")
            continue

        print(f"[{repo_key}] Loading graph from index.db...")
        graph = load_repo_graph(index_path)
        n_nodes = len(graph["nodes"])
        n_edges = len(graph["edges"])
        print(f"  Nodes: {n_nodes:,}, Edges: {n_edges:,}")

        if n_nodes == 0 or n_edges == 0:
            print(f"  SKIP: empty graph")
            continue

        # Build sparse adjacency (undirected for PPR)
        uid_list = list(graph["nodes"].keys())
        uid_to_idx = {uid: i for i, uid in enumerate(uid_list)}
        splade_vecs = graph["splade_vecs"]
        n_splade = len(splade_vecs)
        print(f"  SPLADE vectors: {n_splade:,} ({n_splade/max(n_nodes,1)*100:.0f}% coverage)")

        # Pre-build SPLADE sparse matrix for fast dot products
        splade_matrix = build_splade_matrix(uid_list, splade_vecs)

        rows, cols = [], []
        for src, dst in graph["edges"]:
            si, di = uid_to_idx.get(src), uid_to_idx.get(dst)
            if si is not None and di is not None:
                rows.extend([si, di])
                cols.extend([di, si])

        adj = sparse.csr_matrix(
            (np.ones(len(rows), dtype=np.float32), (rows, cols)),
            shape=(n_nodes, n_nodes),
        )

        # Process queries from GT worktrees
        repo_query_count = 0
        repo_results: list[dict] = []

        for gt_dir in gt_dirs:
            queries_file = gt_dir / "ground_truth" / "queries.jsonl"
            touched_file = gt_dir / "ground_truth" / "touched_objects.jsonl"

            if not queries_file.exists() or not touched_file.exists():
                continue

            # Load GT
            with open(touched_file) as f:
                touched_objects = [json.loads(line) for line in f if line.strip()]

            gt_uids = resolve_gt(touched_objects, graph)
            if len(gt_uids) < 2:
                continue

            # Load queries (only those with seeds/pins)
            with open(queries_file) as f:
                queries = [json.loads(line) for line in f if line.strip()]

            for query in queries:
                if not query.get("seeds") and not query.get("pins"):
                    continue

                seed_uids = resolve_seeds(query, graph)
                if len(seed_uids) < 1:
                    continue

                result = evaluate_query(
                    graph, seed_uids, gt_uids, uid_to_idx, uid_list, adj, splade_matrix,
                )
                if result is not None:
                    result["repo"] = repo_key
                    result["query_id"] = query["query_id"]
                    result["query_type"] = query["query_type"]
                    result["n_seeds"] = len(seed_uids)
                    result["n_gt"] = len(gt_uids)
                    repo_results.append(result)
                    repo_query_count += 1

                if max_queries_per_repo and repo_query_count >= max_queries_per_repo:
                    break

            if max_queries_per_repo and repo_query_count >= max_queries_per_repo:
                break

        print(f"  Evaluated {repo_query_count} queries")
        all_results.extend(repo_results)
        repo_stats.append({
            "repo": repo_key,
            "nodes": n_nodes,
            "edges": n_edges,
            "queries": repo_query_count,
        })

    # ─── Aggregate Results ────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"\nTotal queries evaluated: {len(all_results)}")
    print(f"Repos used: {len(repo_stats)}")
    print()

    if not all_results:
        print("ERROR: No valid queries found!")
        return

    # Aggregate metrics
    metrics = ["recall@10", "recall@20", "recall@50", "recall@100", "mrr"]
    method_names = ["hop1", "ppr", "splade", "fusion"]

    print(f"  {'Metric':<20}", end="")
    for m in method_names:
        print(f" {m:>10}", end="")
    print()
    print(f"  {'-'*20}", end="")
    for _ in method_names:
        print(f" {'-'*10}", end="")
    print()

    for metric in metrics:
        print(f"  {metric:<20}", end="")
        for method in method_names:
            vals = [r[f"{method}_{metric}"] for r in all_results if f"{method}_{metric}" in r]
            if vals:
                print(f" {np.mean(vals):>10.4f}", end="")
            else:
                print(f" {'N/A':>10}", end="")
        print()

    # Lift table vs hop1
    print()
    print(f"  {'Metric':<20} {'PPR lift':>10} {'SPLADE lift':>10} {'Fusion lift':>10}")
    print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*10}")
    for metric in metrics:
        h1_vals = [r[f"hop1_{metric}"] for r in all_results if f"hop1_{metric}" in r]
        if not h1_vals:
            continue
        h1_mean = np.mean(h1_vals)
        lifts = []
        for method in ["ppr", "splade", "fusion"]:
            vals = [r[f"{method}_{metric}"] for r in all_results if f"{method}_{metric}" in r]
            if vals:
                delta = np.mean(vals) - h1_mean
                lift = (delta / max(h1_mean, 1e-10)) * 100
                lifts.append(f"{lift:>+9.1f}%")
            else:
                lifts.append(f"{'N/A':>10}")
        print(f"  {metric:<20} {lifts[0]:>10} {lifts[1]:>10} {lifts[2]:>10}")

    # Discovery analysis
    print()
    print("  DISCOVERY ANALYSIS:")
    total_gt = sum(r["gt_total"] for r in all_results)
    total_hop1_found = sum(r["hop1_gt_found"] for r in all_results)
    total_ppr_found = sum(r["ppr_gt_found"] for r in all_results)
    total_splade_found = sum(r["splade_gt_found"] for r in all_results)
    total_fusion_found = sum(r["fusion_gt_found"] for r in all_results)

    print(f"    Total GT items (excl seeds):    {total_gt:,}")
    print(f"    Found by 1-hop:                 {total_hop1_found:,} ({total_hop1_found/max(total_gt,1)*100:.1f}%)")
    print(f"    Found by PPR (score > 0):       {total_ppr_found:,} ({total_ppr_found/max(total_gt,1)*100:.1f}%)")
    print(f"    Found by SPLADE (score > 0):    {total_splade_found:,} ({total_splade_found/max(total_gt,1)*100:.1f}%)")
    print(f"    Found by Fusion (score > 0):    {total_fusion_found:,} ({total_fusion_found/max(total_gt,1)*100:.1f}%)")
    total_fusion_extra = sum(r["fusion_extra_over_ppr"] for r in all_results)
    print(f"    Fusion extra (over PPR alone):  {total_fusion_extra:,} ({total_fusion_extra/max(total_gt,1)*100:.1f}%)")

    # Per query-type breakdown
    print()
    print("  PER QUERY-TYPE (Recall@20):")
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in all_results:
        by_type[r["query_type"]].append(r)

    print(f"    {'Type':<20} {'N':>5} {'1-Hop':>8} {'PPR':>8} {'SPLADE':>8} {'Fusion':>8}")
    print(f"    {'-'*20} {'-'*5} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for qtype in sorted(by_type.keys()):
        items = by_type[qtype]
        n = len(items)
        h1 = np.mean([r["hop1_recall@20"] for r in items])
        ppr = np.mean([r["ppr_recall@20"] for r in items])
        sp = np.mean([r["splade_recall@20"] for r in items])
        fu = np.mean([r["fusion_recall@20"] for r in items])
        print(f"    {qtype:<20} {n:>5} {h1:>8.4f} {ppr:>8.4f} {sp:>8.4f} {fu:>8.4f}")

    # MRR breakdown
    print()
    print("  PER QUERY-TYPE (MRR):")
    print(f"    {'Type':<20} {'N':>5} {'1-Hop':>8} {'PPR':>8} {'SPLADE':>8} {'Fusion':>8}")
    print(f"    {'-'*20} {'-'*5} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for qtype in sorted(by_type.keys()):
        items = by_type[qtype]
        n = len(items)
        h1 = np.mean([r["hop1_mrr"] for r in items])
        ppr = np.mean([r["ppr_mrr"] for r in items])
        sp = np.mean([r["splade_mrr"] for r in items])
        fu = np.mean([r["fusion_mrr"] for r in items])
        print(f"    {qtype:<20} {n:>5} {h1:>8.4f} {ppr:>8.4f} {sp:>8.4f} {fu:>8.4f}")

    print()
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-repos", type=int, default=None)
    parser.add_argument("--max-queries-per-repo", type=int, default=None)
    args = parser.parse_args()

    run_experiment(
        max_repos=args.max_repos,
        max_queries_per_repo=args.max_queries_per_repo,
    )
