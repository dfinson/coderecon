"""Measure production pipeline recall gap.

Runs the full 6-harvester pipeline (raw_signals_pipeline) on each GT query
and measures what fraction of GT objects end up in the candidate pool.

Compares this against what the SPLADE seed-centroid approach could add.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from scipy import sparse

# Suppress verbose logging from pipeline internals
os.environ.setdefault("STRUCTLOG_LEVEL", "ERROR")
logging.disable(logging.DEBUG)

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from coderecon.mcp.context import AppContext  # noqa: E402
from coderecon.mcp.tools.recon.raw_signals import raw_signals_pipeline  # noqa: E402

# Silence structlog after imports
import structlog  # noqa: E402
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
)
logging.getLogger().setLevel(logging.CRITICAL)

# ─── Configuration ───────────────────────────────────────────────────────────

DATA_ROOT = Path(os.path.expanduser("~/.recon/recon-lab/data"))
CLONES_ROOT = Path(os.path.expanduser("~/.recon/recon-lab/clones"))

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

SPLADE_VOCAB_SIZE = 30522


# ─── GT Resolution (same as ppr_fresh_experiment.py) ─────────────────────────


def resolve_gt_uids(
    touched_objects: list[dict],
    conn: sqlite3.Connection,
) -> set[str]:
    """Resolve GT touched_objects to def_uids in the index.db."""
    cursor = conn.cursor()

    # Load files for path resolution
    cursor.execute("SELECT id, path FROM files")
    file_id_to_path = {row[0]: row[1] for row in cursor.fetchall()}
    path_to_file_id = {v: k for k, v in file_id_to_path.items()}

    # Build candidate_key → def_uid map
    cursor.execute("SELECT def_uid, file_id, kind, name, start_line FROM def_facts")
    ck_map: dict[str, str] = {}
    # For fuzzy: path+name+kind → def_uid
    fuzzy_map: dict[tuple[str, str, str], str] = {}
    for row in cursor.fetchall():
        uid, file_id, kind, name, start_line = row
        path = file_id_to_path.get(file_id, "")
        ckey = f"{path}:{kind}:{name}:{start_line}"
        ck_map[ckey] = uid
        fuzzy_map[(path, name, kind)] = uid

    resolved: set[str] = set()
    for obj in touched_objects:
        ckey = f"{obj['path']}:{obj['kind']}:{obj['name']}:{obj['start_line']}"
        if ckey in ck_map:
            resolved.add(ck_map[ckey])
            continue
        # Fuzzy: same path + name + kind (handles line drift)
        fkey = (obj["path"], obj["name"], obj["kind"])
        if fkey in fuzzy_map:
            resolved.add(fuzzy_map[fkey])

    return resolved


def resolve_seed_uids(
    query: dict,
    conn: sqlite3.Connection,
) -> set[str]:
    """Resolve query seeds/pins to def_uids."""
    cursor = conn.cursor()

    cursor.execute("SELECT id, path FROM files")
    file_id_to_path = {row[0]: row[1] for row in cursor.fetchall()}
    path_to_file_id = {v: k for k, v in file_id_to_path.items()}

    cursor.execute("SELECT def_uid, file_id, name FROM def_facts")
    name_to_uids: dict[str, list[str]] = defaultdict(list)
    file_to_uids: dict[int, list[str]] = defaultdict(list)
    for row in cursor.fetchall():
        uid, file_id, name = row
        if name:
            name_to_uids[name].append(uid)
        file_to_uids[file_id].append(uid)

    resolved: set[str] = set()

    for seed_name in query.get("seeds", []):
        if seed_name in name_to_uids:
            resolved.update(name_to_uids[seed_name])
        if "." in seed_name:
            short = seed_name.rsplit(".", 1)[-1]
            if short in name_to_uids:
                resolved.update(name_to_uids[short])

    for pin_path in query.get("pins", []):
        fid = path_to_file_id.get(pin_path)
        if fid is not None:
            resolved.update(file_to_uids.get(fid, []))

    return resolved


# ─── SPLADE Centroid (for comparison) ────────────────────────────────────────


def decode_splade_blob(blob: bytes) -> dict[int, float]:
    result = {}
    for i in range(0, len(blob), 8):
        tid = struct.unpack("<I", blob[i : i + 4])[0]
        weight = struct.unpack("<f", blob[i + 4 : i + 8])[0]
        result[tid] = weight
    return result


def build_repo_splade_matrix(
    conn: sqlite3.Connection,
    all_uids: list[str],
) -> tuple[sparse.csr_matrix, dict[str, int]]:
    """Build sparse SPLADE matrix once per repo. Returns (matrix, uid_to_idx)."""
    uid_to_idx = {u: i for i, u in enumerate(all_uids)}
    cursor = conn.cursor()
    cursor.execute("SELECT def_uid, vector_blob FROM splade_vecs")

    rows, cols, data = [], [], []
    for row in cursor.fetchall():
        uid, blob = row
        if blob and uid in uid_to_idx:
            idx = uid_to_idx[uid]
            vec = decode_splade_blob(blob)
            for tid, w in vec.items():
                rows.append(idx)
                cols.append(tid)
                data.append(w)

    n = len(all_uids)
    matrix = sparse.csr_matrix(
        (np.array(data, dtype=np.float32), (rows, cols)),
        shape=(n, SPLADE_VOCAB_SIZE),
    )
    return matrix, uid_to_idx


def compute_splade_centroid_topk(
    seed_uids: set[str],
    uid_to_idx: dict[str, int],
    uid_list: list[str],
    splade_matrix: sparse.csr_matrix,
    budget: int,
) -> set[str]:
    """Return top-budget def_uids by SPLADE seed-centroid similarity (cached matrix)."""
    seed_indices = np.array([uid_to_idx[u] for u in seed_uids if u in uid_to_idx])
    if len(seed_indices) == 0:
        return set()

    centroid = splade_matrix[seed_indices].mean(axis=0)
    scores = splade_matrix.dot(centroid.T).A.flatten()

    # Zero out seeds
    for si in seed_indices:
        scores[si] = 0.0

    top_idx = np.argsort(-scores)[:budget]
    return {uid_list[i] for i in top_idx}


# ─── Main ────────────────────────────────────────────────────────────────────

OUTPUT_FILE = Path(__file__).resolve().parent / "pipeline_recall_gap_results.txt"


async def measure_recall_gap():
    out = open(OUTPUT_FILE, "w")

    def log(msg: str = ""):
        out.write(msg + "\n")
        out.flush()

    log("=" * 70)
    log("PIPELINE RECALL GAP: What fraction of GT does the current pipeline miss?")
    log("=" * 70)
    log()

    total_gt = 0
    total_in_pool = 0
    total_centroid_would_add = 0
    total_queries = 0
    per_repo_stats: list[dict] = []

    # Sample: 5 queries per repo from 4 diverse repos for fast directional answer
    SAMPLE_REPOS = ["eval/gin", "eval/pydantic", "eval/axum", "eval/Catch2"]
    MAX_QUERIES_PER_REPO = 5

    repo_items = [(k, v) for k, v in REPO_MAP.items() if k in SAMPLE_REPOS]

    for repo_key, repo_info in repo_items:
        gt_prefix = repo_info["gt_prefix"]
        index_rel = repo_info["index"]
        recon_dir = CLONES_ROOT / index_rel / ".recon"
        index_path = recon_dir / "index.db"
        tantivy_path = recon_dir / "tantivy"
        repo_root = CLONES_ROOT / index_rel

        if not index_path.exists():
            log(f"  SKIP {repo_key}: no index.db")
            continue

        if not tantivy_path.exists():
            log(f"  SKIP {repo_key}: no tantivy index")
            continue

        # Find GT worktree directories
        gt_dirs = sorted([
            d for d in DATA_ROOT.iterdir()
            if d.is_dir() and d.name.startswith(gt_prefix + "_pr")
        ])

        if not gt_dirs:
            log(f"  SKIP {repo_key}: no GT worktrees")
            continue

        log(f"[{repo_key}] Setting up pipeline context...")

        # Create app context for this repo
        ctx = AppContext.standalone(
            repo_root=repo_root,
            db_path=index_path,
            tantivy_path=tantivy_path,
        )
        await ctx.coordinator.load_existing()

        # Open raw connection for GT resolution + centroid
        conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)

        # Get all def_uids for centroid computation
        cursor = conn.cursor()
        cursor.execute("SELECT def_uid FROM def_facts")
        all_uids = [row[0] for row in cursor.fetchall()]

        # Pre-build SPLADE matrix once for this repo
        log(f"  Building SPLADE matrix ({len(all_uids):,} nodes)...")
        splade_matrix, splade_uid_to_idx = build_repo_splade_matrix(conn, all_uids)

        repo_gt_total = 0
        repo_in_pool = 0
        repo_centroid_adds = 0
        repo_queries = 0
        t_repo_start = time.monotonic()

        for gt_dir in gt_dirs:
            queries_file = gt_dir / "ground_truth" / "queries.jsonl"
            touched_file = gt_dir / "ground_truth" / "touched_objects.jsonl"

            if not queries_file.exists() or not touched_file.exists():
                continue

            with open(touched_file) as f:
                touched_objects = [json.loads(line) for line in f if line.strip()]

            gt_uids = resolve_gt_uids(touched_objects, conn)
            if len(gt_uids) < 2:
                continue

            with open(queries_file) as f:
                queries = [json.loads(line) for line in f if line.strip()]

            for query in queries:
                if not query.get("seeds") and not query.get("pins"):
                    continue

                seed_uids = resolve_seed_uids(query, conn)
                if len(seed_uids) < 1:
                    continue

                # Remove seeds from GT
                gt_non_seed = gt_uids - seed_uids
                if not gt_non_seed:
                    continue

                # Run full pipeline
                try:
                    raw = await raw_signals_pipeline(
                        ctx,
                        query["query_text"],
                        seeds=query.get("seeds"),
                        pins=query.get("pins"),
                    )
                except Exception as e:
                    # Skip queries that fail (e.g. tantivy issues)
                    continue

                candidates = raw["candidates"]
                pool_uids = {c["def_uid"] for c in candidates}

                # How many GT items are in the pool?
                found = gt_non_seed & pool_uids
                missed = gt_non_seed - pool_uids

                repo_gt_total += len(gt_non_seed)
                repo_in_pool += len(found)
                repo_queries += 1

                # Would SPLADE centroid find the missed ones?
                # Use same budget as current pool size
                if missed:
                    centroid_pool = compute_splade_centroid_topk(
                        seed_uids, splade_uid_to_idx, all_uids,
                        splade_matrix, budget=len(pool_uids),
                    )
                    centroid_adds = missed & centroid_pool
                    repo_centroid_adds += len(centroid_adds)

                if repo_queries >= MAX_QUERIES_PER_REPO:
                    break
            if repo_queries >= MAX_QUERIES_PER_REPO:
                break

        conn.close()

        if repo_queries > 0:
            elapsed = time.monotonic() - t_repo_start
            recall = repo_in_pool / max(repo_gt_total, 1)
            log(
                f"  {repo_queries} queries in {elapsed:.0f}s | "
                f"GT recall: {repo_in_pool}/{repo_gt_total} ({recall:.1%}) | "
                f"Centroid would add: {repo_centroid_adds}"
            )
        else:
            log(f"  No valid queries")

        total_gt += repo_gt_total
        total_in_pool += repo_in_pool
        total_centroid_would_add += repo_centroid_adds
        total_queries += repo_queries
        per_repo_stats.append({
            "repo": repo_key,
            "queries": repo_queries,
            "gt_total": repo_gt_total,
            "in_pool": repo_in_pool,
            "centroid_adds": repo_centroid_adds,
        })

    # ─── Summary ──────────────────────────────────────────────────────────────
    log()
    log("=" * 70)
    log("SUMMARY")
    log("=" * 70)
    log(f"\nTotal queries: {total_queries}")
    log(f"Total GT items (excl seeds): {total_gt:,}")
    log(f"Found by current pipeline:   {total_in_pool:,} ({total_in_pool/max(total_gt,1):.1%})")
    missed_total = total_gt - total_in_pool
    log(f"Missed by current pipeline:  {missed_total:,} ({missed_total/max(total_gt,1):.1%})")
    log(
        f"SPLADE centroid would add:   {total_centroid_would_add:,} "
        f"({total_centroid_would_add/max(missed_total,1):.1%} of missed)"
    )
    log(
        f"Potential new recall:        "
        f"{(total_in_pool + total_centroid_would_add)/max(total_gt,1):.1%}"
    )

    log("\n  Per-repo breakdown:")
    log(f"  {'Repo':<40} {'Queries':>7} {'GT':>8} {'Found':>8} {'Recall':>8} {'Centroid+':>10}")
    log(f"  {'-'*40} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*10}")
    for s in per_repo_stats:
        if s["gt_total"] > 0:
            recall = s["in_pool"] / s["gt_total"]
            log(
                f"  {s['repo']:<40} {s['queries']:>7} "
                f"{s['gt_total']:>8} {s['in_pool']:>8} "
                f"{recall:>7.1%} {s['centroid_adds']:>10}"
            )

    out.close()
    print(f"Results written to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(measure_recall_gap())
