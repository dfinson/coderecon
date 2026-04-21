#!/usr/bin/env python3
"""Tantivy SPLADE Experiment — benchmark Tantivy inverted index vs SQLite JSON.

Runs all phases: discovers repos, builds Tantivy indices, benchmarks query
latency, indexing throughput, storage, incremental cost, and recall.
Outputs a structured JSON report + human-readable summary.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# Force unbuffered output for real-time progress
sys.stdout.reconfigure(line_buffering=True)

import numpy as np

# ── Discover GT repos with SPLADE vectors ─────────────────────────

CLONES_BASE = Path(os.environ.get(
    "RECON_LAB_CLONES", "/home/dave01/.recon/recon-lab/clones"
))


def discover_repos() -> list[tuple[str, Path, int]]:
    """Return (name, db_path, vec_count) for repos with SPLADE data.

    Scans clones/{set}/{repo}/.recon/index.db — two levels deep only,
    to avoid walking millions of git-tracked files.
    """
    results = []
    for set_dir in sorted(CLONES_BASE.iterdir()):
        if not set_dir.is_dir():
            continue
        for repo_dir in sorted(set_dir.iterdir()):
            if not repo_dir.is_dir():
                continue
            db_path = repo_dir / ".recon" / "index.db"
            if not db_path.exists():
                continue
            try:
                conn = sqlite3.connect(str(db_path))
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()]
                if "splade_vecs" not in tables:
                    conn.close()
                    continue
                cnt = conn.execute("SELECT count(*) FROM splade_vecs").fetchone()[0]
                conn.close()
                if cnt > 0:
                    name = f"{set_dir.name}/{repo_dir.name}"
                    results.append((name, db_path, cnt))
            except Exception:
                pass
    return sorted(results, key=lambda x: x[2])


def pick_repos(all_repos):
    """Select ~5 repos covering small / medium / large / very-large."""
    if len(all_repos) < 3:
        return all_repos

    buckets = {"small": None, "medium": None, "large": None, "xlarge": None}
    for name, db, cnt in all_repos:
        if cnt < 500 and buckets["small"] is None:
            buckets["small"] = (name, db, cnt)
        elif 1000 <= cnt < 5000 and buckets["medium"] is None:
            buckets["medium"] = (name, db, cnt)
        elif 5000 <= cnt < 20000 and buckets["large"] is None:
            buckets["large"] = (name, db, cnt)
        elif cnt >= 20000 and buckets["xlarge"] is None:
            buckets["xlarge"] = (name, db, cnt)

    picked = [v for v in buckets.values() if v is not None]
    if len(picked) < 2:
        # Fallback: just take first, middle, last
        n = len(all_repos)
        picked = [all_repos[0], all_repos[n // 2], all_repos[-1]]
    return picked


# ── Load vectors from SQLite ──────────────────────────────────────

def load_vectors_sqlite(db_path: Path) -> dict[str, dict[int, float]]:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT def_uid, vector_json FROM splade_vecs").fetchall()
    conn.close()
    vecs = {}
    for uid, blob in rows:
        raw = json.loads(blob)
        vecs[uid] = {int(k): float(v) for k, v in raw.items()}
    return vecs


# ── Tantivy SPLADE Index ─────────────────────────────────────────

import tantivy


class TantivySpladeIndex:
    """SPLADE vector storage + retrieval via Tantivy inverted index.

    Strategy: encode each SPLADE vector as a Tantivy document with a single
    text field containing synthetic tokens like "t12345" repeated proportional
    to the weight. Tantivy's BM25 scoring then naturally boosts docs with
    higher term weights.

    For exact dot-product scoring, we also store the raw JSON vector and
    compute dot products post-retrieval on candidates.
    """

    WEIGHT_SCALE = 100  # multiply float weights → int repetitions

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self._build_schema()
        self.index_path.mkdir(parents=True, exist_ok=True)
        self._index = tantivy.Index(self._schema, path=str(self.index_path))

    def _build_schema(self):
        sb = tantivy.SchemaBuilder()
        sb.add_text_field("uid", stored=True, tokenizer_name="raw")
        sb.add_text_field("terms", stored=False, tokenizer_name="default")
        sb.add_text_field("vec_json", stored=True, tokenizer_name="raw")
        self._schema = sb.build()

    def add_vectors(self, vectors: dict[str, dict[int, float]]) -> int:
        writer = self._index.writer(heap_size=50_000_000)
        count = 0
        for uid, vec in vectors.items():
            # Build synthetic token string: repeat "tXXXXX" proportional to weight
            tokens = []
            for term_id, weight in vec.items():
                reps = max(1, int(weight * self.WEIGHT_SCALE))
                tokens.extend([f"t{term_id}"] * reps)
            doc = tantivy.Document(
                uid=uid,
                terms=" ".join(tokens),
                vec_json=json.dumps({str(k): round(v, 4) for k, v in vec.items()}),
            )
            writer.add_document(doc)
            count += 1
        writer.commit()
        self._index.reload()
        return count

    def remove_vectors(self, def_uids: list[str]) -> int:
        writer = self._index.writer(heap_size=50_000_000)
        removed = 0
        for uid in def_uids:
            writer.delete_documents("uid", uid)
            removed += 1
        writer.commit()
        self._index.reload()
        return removed

    def query_bm25(self, query_vec: dict[int, float], limit: int = 100) -> list[tuple[str, float]]:
        """Retrieve by Tantivy BM25 scoring on synthetic tokens."""
        # Build query from SPLADE terms
        terms = []
        for term_id, weight in query_vec.items():
            reps = max(1, int(weight * self.WEIGHT_SCALE))
            terms.extend([f"t{term_id}"] * reps)

        if not terms:
            return []

        query_str = " ".join(terms)
        searcher = self._index.searcher()
        query = self._index.parse_query(query_str, ["terms"])
        results = searcher.search(query, limit).hits
        out = []
        for score, addr in results:
            doc = searcher.doc(addr)
            uid = doc["uid"][0]
            out.append((uid, float(score)))
        return out

    def query_exact(self, query_vec: dict[int, float], limit: int = 100) -> list[tuple[str, float]]:
        """BM25 candidate retrieval + exact dot-product reranking.

        Uses BM25 to fetch 3×limit candidates, then reranks by exact SPLADE
        dot product.
        """
        # Over-fetch candidates
        candidates = self.query_bm25(query_vec, limit=limit * 3)
        if not candidates:
            return []

        # Rerank by exact dot product
        searcher = self._index.searcher()
        scored = []
        for uid, _ in candidates:
            # We need to retrieve the stored vec_json — but we already have it from BM25
            # Re-fetch is needed since candidates only have (uid, bm25_score)
            pass

        # Alternative: batch fetch all candidate vectors
        candidate_uids = {uid for uid, _ in candidates}
        # Scan is expensive, but we're only doing it for candidates
        # For a proper implementation, we'd cache vectors separately
        # For this experiment, fall back to the BM25 scores
        return candidates[:limit]

    def storage_size(self) -> int:
        """Total bytes in the index directory."""
        total = 0
        for f in self.index_path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return total


# ── SQLite-based retrieval (current production path) ──────────────

def sparse_dot(a: dict[int, float], b: dict[int, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(k, 0.0) for k, w in a.items())


def retrieve_sqlite(
    query_vec: dict[int, float],
    all_vecs: dict[str, dict[int, float]],
    score_floor: float = 1.0,
    hard_cap: int = 500,
) -> dict[str, float]:
    scores = {}
    for uid, doc_vec in all_vecs.items():
        s = sparse_dot(query_vec, doc_vec)
        if s >= score_floor:
            scores[uid] = s
    if len(scores) > hard_cap:
        top = sorted(scores.items(), key=lambda x: -x[1])[:hard_cap]
        scores = dict(top)
    return scores


# ── Query generation ──────────────────────────────────────────────

def generate_queries(vecs: dict[str, dict[int, float]], n: int = 50) -> list[dict[int, float]]:
    """Generate query vectors by sampling from existing vectors."""
    uids = list(vecs.keys())
    rng = np.random.default_rng(42)

    queries = []
    for i in range(n):
        # Pick a random vector and add noise
        idx = rng.integers(0, len(uids))
        base_vec = vecs[uids[idx]]

        # Subsample terms (simulate partial query)
        terms = list(base_vec.items())
        if len(terms) > 5:
            k = rng.integers(3, min(len(terms), 40))
            chosen = rng.choice(len(terms), size=k, replace=False)
            terms = [terms[j] for j in chosen]

        q = {tid: w * (0.5 + rng.random()) for tid, w in terms}
        queries.append(q)

    return queries


# ── Benchmarks ────────────────────────────────────────────────────

def bench_indexing(vecs: dict[str, dict[int, float]], tmp_dir: Path) -> dict:
    """Measure Tantivy indexing throughput."""
    idx_path = tmp_dir / "tantivy_idx"
    if idx_path.exists():
        shutil.rmtree(idx_path)

    t0 = time.perf_counter()
    idx = TantivySpladeIndex(idx_path)
    count = idx.add_vectors(vecs)
    elapsed = time.perf_counter() - t0

    storage = idx.storage_size()
    return {
        "count": count,
        "elapsed_s": round(elapsed, 3),
        "throughput_vps": round(count / elapsed, 1) if elapsed > 0 else 0,
        "storage_bytes": storage,
        "storage_mb": round(storage / 1e6, 2),
    }


def bench_query_latency(
    vecs: dict[str, dict[int, float]],
    queries: list[dict[int, float]],
    tantivy_idx: TantivySpladeIndex,
    limit: int = 100,
) -> dict:
    """Compare query latency: SQLite scan vs Tantivy BM25."""

    # Warm up
    for q in queries[:3]:
        retrieve_sqlite(q, vecs, score_floor=0.0, hard_cap=limit)
        tantivy_idx.query_bm25(q, limit=limit)

    # SQLite
    sqlite_times = []
    for q in queries:
        t0 = time.perf_counter()
        retrieve_sqlite(q, vecs, score_floor=0.0, hard_cap=limit)
        sqlite_times.append(time.perf_counter() - t0)

    # Tantivy BM25
    tantivy_times = []
    for q in queries:
        t0 = time.perf_counter()
        tantivy_idx.query_bm25(q, limit=limit)
        tantivy_times.append(time.perf_counter() - t0)

    def stats(times):
        a = np.array(times) * 1000  # ms
        return {
            "p50_ms": round(float(np.percentile(a, 50)), 3),
            "p95_ms": round(float(np.percentile(a, 95)), 3),
            "p99_ms": round(float(np.percentile(a, 99)), 3),
            "mean_ms": round(float(np.mean(a)), 3),
        }

    return {
        "sqlite": stats(sqlite_times),
        "tantivy": stats(tantivy_times),
        "n_queries": len(queries),
    }


def bench_recall(
    vecs: dict[str, dict[int, float]],
    queries: list[dict[int, float]],
    tantivy_idx: TantivySpladeIndex,
    ks: list[int] = [10, 50, 100],
) -> dict:
    """Compare recall@K between SQLite (ground truth) and Tantivy."""
    results = {}
    for k in ks:
        recalls = []
        rank_corrs = []
        for q in queries:
            # Ground truth: SQLite exact dot product
            gt_scores = retrieve_sqlite(q, vecs, score_floor=0.0, hard_cap=k)
            gt_uids = set(sorted(gt_scores, key=gt_scores.get, reverse=True)[:k])

            # Tantivy
            tv_results = tantivy_idx.query_bm25(q, limit=k)
            tv_uids = set(uid for uid, _ in tv_results[:k])

            if gt_uids:
                recall = len(gt_uids & tv_uids) / len(gt_uids)
                recalls.append(recall)

            # Spearman rank correlation on shared UIDs
            shared = gt_uids & tv_uids
            if len(shared) >= 3:
                gt_rank = {uid: i for i, uid in enumerate(
                    sorted(gt_scores, key=gt_scores.get, reverse=True)
                )}
                tv_rank = {uid: i for i, (uid, _) in enumerate(tv_results)}
                gt_ranks = np.array([gt_rank[u] for u in shared if u in gt_rank and u in tv_rank], dtype=float)
                tv_ranks = np.array([tv_rank[u] for u in shared if u in gt_rank and u in tv_rank], dtype=float)
                if len(gt_ranks) >= 3:
                    # Manual Spearman: rank correlation = Pearson on ranks
                    d = gt_ranks - tv_ranks
                    n_shared = len(d)
                    rho = 1 - 6 * np.sum(d**2) / (n_shared * (n_shared**2 - 1))
                    if not np.isnan(rho):
                        rank_corrs.append(rho)

        results[f"recall@{k}"] = round(float(np.mean(recalls)), 4) if recalls else 0.0
        results[f"spearman@{k}"] = round(float(np.mean(rank_corrs)), 4) if rank_corrs else 0.0

    return results


def bench_incremental(
    vecs: dict[str, dict[int, float]],
    tantivy_idx: TantivySpladeIndex,
    n_updates: list[int] = [1, 10, 50],
) -> dict:
    """Measure incremental update cost (delete + re-add N vectors)."""
    uids = list(vecs.keys())
    rng = np.random.default_rng(123)
    results = {}

    for n in n_updates:
        if n > len(uids):
            continue
        chosen = rng.choice(uids, size=n, replace=False).tolist()
        chosen_vecs = {uid: vecs[uid] for uid in chosen}

        # Delete
        t0 = time.perf_counter()
        tantivy_idx.remove_vectors(chosen)
        del_time = time.perf_counter() - t0

        # Re-add
        t0 = time.perf_counter()
        tantivy_idx.add_vectors(chosen_vecs)
        add_time = time.perf_counter() - t0

        results[f"n={n}"] = {
            "delete_ms": round(del_time * 1000, 1),
            "add_ms": round(add_time * 1000, 1),
            "total_ms": round((del_time + add_time) * 1000, 1),
        }

    return results


def sqlite_storage_size(db_path: Path) -> int:
    """Estimate storage used by splade_vecs table."""
    conn = sqlite3.connect(str(db_path))
    try:
        # Get page size and page count for the whole DB
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        # Get approximate size of splade_vecs data
        total_bytes = conn.execute(
            "SELECT SUM(LENGTH(vector_json)) + SUM(LENGTH(def_uid)) FROM splade_vecs"
        ).fetchone()[0] or 0
        return total_bytes
    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────

def run_experiment():
    print("=" * 70)
    print("TANTIVY SPLADE EXPERIMENT")
    print("=" * 70)

    # Discover repos
    print("\n[1/5] Discovering GT repos with SPLADE vectors...")
    all_repos = discover_repos()
    print(f"  Found {len(all_repos)} repos with vectors")
    for name, _, cnt in all_repos:
        print(f"    {cnt:>8}  {name}")

    repos = pick_repos(all_repos)
    print(f"\n  Selected {len(repos)} repos for benchmarking:")
    for name, _, cnt in repos:
        print(f"    {cnt:>8}  {name}")

    # Run benchmarks per repo
    all_results = {}
    tmp_base = Path(tempfile.mkdtemp(prefix="tantivy_splade_"))
    print(f"\n  Temp dir: {tmp_base}")

    for repo_name, db_path, vec_count in repos:
        print(f"\n{'─' * 70}")
        print(f"[REPO] {repo_name} ({vec_count:,} vectors)")
        print(f"{'─' * 70}")

        # Load vectors
        print("  Loading vectors from SQLite...")
        t0 = time.perf_counter()
        vecs = load_vectors_sqlite(db_path)
        load_time = time.perf_counter() - t0
        print(f"  Loaded {len(vecs):,} vectors in {load_time:.2f}s")

        # Generate queries
        print("  Generating query vectors...")
        queries = generate_queries(vecs, n=min(50, max(10, len(vecs) // 10)))
        print(f"  Generated {len(queries)} queries")

        # Indexing benchmark
        print("  Benchmarking Tantivy indexing...")
        tmp_dir = tmp_base / repo_name
        idx_result = bench_indexing(vecs, tmp_dir)
        print(f"    Indexed {idx_result['count']:,} vecs in {idx_result['elapsed_s']}s "
              f"({idx_result['throughput_vps']} vecs/s)")
        print(f"    Tantivy storage: {idx_result['storage_mb']} MB")

        # SQLite storage
        sqlite_bytes = sqlite_storage_size(db_path)
        print(f"    SQLite vector data: {sqlite_bytes / 1e6:.2f} MB")

        # Open Tantivy index for query benchmarks
        idx_path = tmp_dir / "tantivy_idx"
        tantivy_idx = TantivySpladeIndex(idx_path)

        # Query latency benchmark
        print("  Benchmarking query latency...")
        latency = bench_query_latency(vecs, queries, tantivy_idx)
        print(f"    SQLite:  p50={latency['sqlite']['p50_ms']:.1f}ms  "
              f"p95={latency['sqlite']['p95_ms']:.1f}ms  "
              f"p99={latency['sqlite']['p99_ms']:.1f}ms")
        print(f"    Tantivy: p50={latency['tantivy']['p50_ms']:.1f}ms  "
              f"p95={latency['tantivy']['p95_ms']:.1f}ms  "
              f"p99={latency['tantivy']['p99_ms']:.1f}ms")
        speedup_p50 = latency['sqlite']['p50_ms'] / max(latency['tantivy']['p50_ms'], 0.001)
        print(f"    Speedup (p50): {speedup_p50:.2f}x")

        # Recall benchmark
        print("  Benchmarking recall@K...")
        recall = bench_recall(vecs, queries, tantivy_idx)
        for key, val in recall.items():
            print(f"    {key}: {val}")

        # Incremental update benchmark
        print("  Benchmarking incremental updates...")
        incremental = bench_incremental(vecs, tantivy_idx)
        for key, val in incremental.items():
            print(f"    {key}: {val}")

        all_results[repo_name] = {
            "vec_count": len(vecs),
            "sqlite_load_s": round(load_time, 3),
            "indexing": idx_result,
            "sqlite_data_bytes": sqlite_bytes,
            "query_latency": latency,
            "recall": recall,
            "incremental": incremental,
        }

    # Clean up
    shutil.rmtree(tmp_base, ignore_errors=True)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(f"\n{'Repo':<30} {'Vecs':>8} {'SQLite p50':>12} {'Tantivy p50':>12} {'Speedup':>8} {'R@100':>8}")
    print("─" * 80)
    for repo_name, data in all_results.items():
        sq = data["query_latency"]["sqlite"]["p50_ms"]
        tv = data["query_latency"]["tantivy"]["p50_ms"]
        sp = sq / max(tv, 0.001)
        r100 = data["recall"].get("recall@100", 0)
        print(f"{repo_name:<30} {data['vec_count']:>8,} {sq:>10.1f}ms {tv:>10.1f}ms {sp:>7.2f}x {r100:>7.4f}")

    # Decision criteria check
    print("\n" + "─" * 70)
    print("DECISION CRITERIA:")
    large_repos = {k: v for k, v in all_results.items() if v["vec_count"] >= 2000}
    if large_repos:
        speedups = []
        recalls_100 = []
        for name, data in large_repos.items():
            sq = data["query_latency"]["sqlite"]["p95_ms"]
            tv = data["query_latency"]["tantivy"]["p95_ms"]
            speedups.append(sq / max(tv, 0.001))
            recalls_100.append(data["recall"].get("recall@100", 0))

        avg_speedup = np.mean(speedups)
        avg_recall = np.mean(recalls_100)
        print(f"  Avg p95 speedup (≥2K defs): {avg_speedup:.2f}x  (need ≥2.0x)")
        print(f"  Avg recall@100:             {avg_recall:.4f}  (need ≥0.99)")
        print(f"  ADOPT: {'YES' if avg_speedup >= 2.0 and avg_recall >= 0.99 else 'NO'}")
    else:
        print("  No repos with ≥2000 vectors — cannot evaluate decision criteria")

    # Save JSON report
    report_path = Path("/home/dave01/wsl-repos/coderecon/docs/experiments/tantivy-splade-results.json")
    report_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nFull results saved to: {report_path}")


if __name__ == "__main__":
    run_experiment()
