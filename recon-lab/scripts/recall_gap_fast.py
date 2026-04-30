"""Recall gap analysis — file-level aware.

In production: harvesters → candidate pool → file ranker (top-20 files) → def ranker.
Only defs in surviving files matter. So we measure:
  1. Which GT FILES does the current pool cover? (at least 1 def from that file)
  2. Would centroid add GT defs in files already in the pool? (free wins)
  3. Would centroid surface new files not in the pool at all? (requires file ranker change)
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import sys
import re
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import sparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
from coderecon.index.search.splade import SpladeEncoder

SPLADE_VOCAB_SIZE = 30522

# Global encoder — loaded once
_encoder: SpladeEncoder | None = None

def get_encoder() -> SpladeEncoder:
    global _encoder
    if _encoder is None:
        _encoder = SpladeEncoder()
        _encoder.load()
    return _encoder
DATA_ROOT = Path(os.path.expanduser("~/.recon/recon-lab/data"))
CLONES_ROOT = Path(os.path.expanduser("~/.recon/recon-lab/clones"))

REPO_MAP = {
    "eval/AutoMapper": {"gt_prefix": "csharp-automapper"},
    "eval/Catch2": {"gt_prefix": "cpp-catch2"},
    "eval/RxSwift": {"gt_prefix": "swift-rxswift"},
    "eval/axum": {"gt_prefix": "rust-axum"},
    "eval/celery": {"gt_prefix": "python-celery"},
    "eval/diesel": {"gt_prefix": "rust-diesel"},
    "eval/esbuild": {"gt_prefix": "typescript-esbuild"},
    "eval/fiber": {"gt_prefix": "go-fiber"},
    "eval/gin": {"gt_prefix": "go-gin"},
    "eval/grpc": {"gt_prefix": "cpp-grpc"},
    "eval/lombok": {"gt_prefix": "java-lombok"},
    "eval/mockito": {"gt_prefix": "java-mockito"},
    "eval/pydantic": {"gt_prefix": "python-pydantic"},
    "eval/sinatra": {"gt_prefix": "ruby-sinatra"},
    "eval/swift-composable-architecture": {"gt_prefix": "swift-composable-architecture"},
    "eval/vitest": {"gt_prefix": "typescript-vitest"},
    "eval/whoops": {"gt_prefix": "php-whoops"},
    "eval/xunit": {"gt_prefix": "csharp-xunit"},
    "ranker-gate/Alamofire": {"gt_prefix": "swift-alamofire"},
}

OUTPUT = Path(__file__).resolve().parent / "pipeline_recall_gap_results.txt"


def decode_splade_blob(blob: bytes) -> dict[int, float]:
    result = {}
    for i in range(0, len(blob), 8):
        tid = struct.unpack("<I", blob[i : i + 4])[0]
        weight = struct.unpack("<f", blob[i + 4 : i + 8])[0]
        result[tid] = weight
    return result


class RepoIndex:
    """Pre-loads everything from index.db for fast per-query simulation."""

    def __init__(self, index_path: Path):
        conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        cur = conn.cursor()

        # Files
        cur.execute("SELECT id, path FROM files")
        self.file_id_to_path = {r[0]: r[1] for r in cur.fetchall()}
        self.path_to_file_id = {v: k for k, v in self.file_id_to_path.items()}

        # Defs
        cur.execute("SELECT def_uid, file_id, kind, name, start_line FROM def_facts")
        self.nodes: dict[str, dict] = {}
        self.name_to_uids: dict[str, list[str]] = defaultdict(list)
        self.file_to_uids: dict[int, list[str]] = defaultdict(list)
        self.path_to_uids: dict[str, list[str]] = defaultdict(list)
        self.ck_map: dict[str, str] = {}
        self.fuzzy_map: dict[tuple[str, str, str], str] = {}

        for row in cur.fetchall():
            uid, file_id, kind, name, start_line = row
            path = self.file_id_to_path.get(file_id, "")
            self.nodes[uid] = {"path": path, "kind": kind, "name": name, "file_id": file_id}
            if name:
                self.name_to_uids[name].append(uid)
            self.file_to_uids[file_id].append(uid)
            self.path_to_uids[path].append(uid)
            ckey = f"{path}:{kind}:{name}:{start_line}"
            self.ck_map[ckey] = uid
            self.fuzzy_map[(path, name, kind)] = uid

        # Graph edges: ref_facts (enclosing def → target)
        cur.execute("""
            SELECT file_id, start_line, target_def_uid FROM ref_facts
            WHERE role = 'reference' AND target_def_uid IS NOT NULL
        """)
        # Build file_id → sorted (start, end, uid) for scope resolution
        file_defs: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
        cur2 = conn.cursor()
        cur2.execute("SELECT def_uid, file_id, start_line, end_line FROM def_facts")
        for r in cur2.fetchall():
            file_defs[r[1]].append((r[2], r[3] or 999999, r[0]))
        for fid in file_defs:
            file_defs[fid].sort()

        self.neighbors: dict[str, set[str]] = defaultdict(set)  # bidirectional
        # Directed: caller → callee (enclosing_def references target_def)
        self.forward_edges: list[tuple[str, str]] = []  # (caller, callee)
        for row in cur.fetchall():
            target_uid = row[2]
            if target_uid not in self.nodes:
                continue
            ref_file_id, ref_line = row[0], row[1]
            defs_in = file_defs.get(ref_file_id, [])
            enclosing = None
            for start, end, uid in reversed(defs_in):
                if start <= ref_line <= end:
                    enclosing = uid
                    break
                if start < ref_line:
                    enclosing = uid
                    break
            if enclosing and enclosing != target_uid:
                self.neighbors[enclosing].add(target_uid)
                self.neighbors[target_uid].add(enclosing)
                self.forward_edges.append((enclosing, target_uid))

        # Interface impl edges
        cur.execute("SELECT implementor_def_uid, interface_def_uid FROM interface_impl_facts")
        for row in cur.fetchall():
            if row[0] in self.nodes and row[1] in self.nodes:
                self.neighbors[row[0]].add(row[1])
                self.neighbors[row[1]].add(row[0])
                self.forward_edges.append((row[0], row[1]))
                self.forward_edges.append((row[1], row[0]))

        # Import edges (file-level)
        try:
            cur.execute("SELECT source_file_id, target_file_id FROM import_facts")
            self.file_imports: dict[int, set[int]] = defaultdict(set)
            self.file_importers: dict[int, set[int]] = defaultdict(set)
            for row in cur.fetchall():
                self.file_imports[row[0]].add(row[1])
                self.file_importers[row[1]].add(row[0])
        except sqlite3.OperationalError:
            self.file_imports = defaultdict(set)
            self.file_importers = defaultdict(set)

        # SPLADE matrix
        uid_list = list(self.nodes.keys())
        self.uid_list = uid_list
        self.uid_to_idx = {u: i for i, u in enumerate(uid_list)}
        n = len(uid_list)

        cur.execute("SELECT def_uid, vector_blob FROM splade_vecs")
        rows_s, cols_s, data_s = [], [], []
        for row in cur.fetchall():
            uid, blob = row
            if blob and uid in self.uid_to_idx:
                idx = self.uid_to_idx[uid]
                vec = decode_splade_blob(blob)
                for tid, w in vec.items():
                    rows_s.append(idx)
                    cols_s.append(tid)
                    data_s.append(w)
        self.splade_matrix = sparse.csr_matrix(
            (np.array(data_s, dtype=np.float32), (rows_s, cols_s)),
            shape=(n, SPLADE_VOCAB_SIZE),
        )

        # Build directed adjacency: forward (caller→callee) and reverse (callee→caller)
        fwd_rows, fwd_cols = [], []
        for src, dst in self.forward_edges:
            si, di = self.uid_to_idx.get(src), self.uid_to_idx.get(dst)
            if si is not None and di is not None:
                fwd_rows.append(si)
                fwd_cols.append(di)
        # Forward: row i has edges to things i calls/uses
        self.adj_forward = sparse.csr_matrix(
            (np.ones(len(fwd_rows), dtype=np.float32), (fwd_rows, fwd_cols)),
            shape=(n, n),
        )
        # Reverse: row i has edges to things that call/use i
        self.adj_reverse = self.adj_forward.T.tocsr()

        conn.close()

    def resolve_gt(self, touched_objects: list[dict]) -> set[str]:
        resolved = set()
        for obj in touched_objects:
            ckey = f"{obj['path']}:{obj['kind']}:{obj['name']}:{obj['start_line']}"
            if ckey in self.ck_map:
                resolved.add(self.ck_map[ckey])
                continue
            fkey = (obj["path"], obj["name"], obj["kind"])
            if fkey in self.fuzzy_map:
                resolved.add(self.fuzzy_map[fkey])
        return resolved

    def resolve_seeds(self, query: dict) -> set[str]:
        resolved = set()
        for seed_name in query.get("seeds", []):
            if seed_name in self.name_to_uids:
                resolved.update(self.name_to_uids[seed_name])
            if "." in seed_name:
                short = seed_name.rsplit(".", 1)[-1]
                if short in self.name_to_uids:
                    resolved.update(self.name_to_uids[short])
        for pin_path in query.get("pins", []):
            fid = self.path_to_file_id.get(pin_path)
            if fid is not None:
                resolved.update(self.file_to_uids.get(fid, []))
        return resolved

    def harvest_graph_1hop(self, seeds: set[str]) -> set[str]:
        """Simulate graph harvester: 1-hop neighbors of seeds."""
        found = set()
        for s in seeds:
            found.update(self.neighbors.get(s, set()))
        return found - seeds

    def harvest_term_match(self, query: dict) -> set[str]:
        """Simulate term harvester: name substring match on query keywords."""
        text = query.get("query_text", "")
        # Extract identifiers from query
        words = set(re.findall(r'[A-Za-z_]\w{2,}', text))
        found = set()
        for w in words:
            wl = w.lower()
            for name, uids in self.name_to_uids.items():
                if wl in name.lower():
                    found.update(uids)
        return found

    def harvest_imports(self, seeds: set[str]) -> set[str]:
        """Simulate import harvester: forward+reverse imports from seed files."""
        seed_files = set()
        for s in seeds:
            info = self.nodes.get(s)
            if info:
                seed_files.add(info["file_id"])

        target_files = set()
        for fid in seed_files:
            target_files.update(self.file_imports.get(fid, set()))
            target_files.update(self.file_importers.get(fid, set()))

        found = set()
        for fid in target_files:
            found.update(self.file_to_uids.get(fid, []))
        return found - seeds

    def harvest_explicit(self, seeds: set[str]) -> set[str]:
        """Simulate explicit harvester: all defs in seed files."""
        seed_files = set()
        for s in seeds:
            info = self.nodes.get(s)
            if info:
                seed_files.add(info["file_id"])

        found = set()
        for fid in seed_files:
            found.update(self.file_to_uids.get(fid, []))
        return found - seeds

    def harvest_splade_query(self, query_text: str, top_n: int = 500) -> set[str]:
        """SPLADE query-text retrieval: encode query, dot-product against stored vecs."""
        enc = get_encoder()
        q_vec = enc.encode_queries([query_text])[0]  # dict[int, float]
        if not q_vec:
            return set()

        # Build sparse query vector and matmul
        q_tids = list(q_vec.keys())
        q_weights = list(q_vec.values())
        q_sparse = sparse.csr_matrix(
            (np.array(q_weights, dtype=np.float32), ([0]*len(q_tids), q_tids)),
            shape=(1, SPLADE_VOCAB_SIZE),
        )
        scores = np.asarray(self.splade_matrix.dot(q_sparse.T).todense()).flatten()
        top_idx = np.argsort(-scores)[:top_n]
        return {self.uid_list[i] for i in top_idx if scores[i] > 0}

    def harvest_splade_centroid(self, seeds: set[str], budget: int) -> set[str]:
        """SPLADE seed-centroid: top-budget by similarity to seed average."""
        seed_indices = np.array([self.uid_to_idx[u] for u in seeds if u in self.uid_to_idx])
        if len(seed_indices) == 0:
            return set()

        centroid = self.splade_matrix[seed_indices].mean(axis=0)  # (1, vocab) matrix
        # Convert centroid to sparse for efficient matmul
        centroid_sparse = sparse.csr_matrix(centroid)
        scores = np.asarray(self.splade_matrix.dot(centroid_sparse.T).todense()).flatten()

        for si in seed_indices:
            scores[si] = 0.0

        top_idx = np.argsort(-scores)[:budget]
        return {self.uid_list[i] for i in top_idx}

    def _ppr(self, adj: sparse.csr_matrix, seed_indices: np.ndarray,
             alpha: float = 0.15, max_iter: int = 50, tol: float = 1e-6) -> np.ndarray:
        """Power-iteration PPR on given adjacency. Returns score vector."""
        n = adj.shape[0]
        if n == 0 or len(seed_indices) == 0:
            return np.zeros(n, dtype=np.float64)

        p = np.zeros(n, dtype=np.float64)
        p[seed_indices] = 1.0 / len(seed_indices)

        row_sums = np.array(adj.sum(axis=1)).flatten()
        row_sums[row_sums == 0] = 1.0
        T = sparse.diags(1.0 / row_sums) @ adj

        x = p.copy()
        for _ in range(max_iter):
            x_new = (1 - alpha) * (T.T @ x) + alpha * p
            if np.abs(x_new - x).sum() < tol:
                break
            x = x_new
        return x

    def harvest_reverse_ppr(self, seeds: set[str], budget: int) -> set[str]:
        """Reverse PPR: find things that DEPEND ON seeds (callers, upstream)."""
        seed_indices = np.array([self.uid_to_idx[u] for u in seeds if u in self.uid_to_idx])
        if len(seed_indices) == 0:
            return set()

        # Reverse graph: edges point from callee → caller
        # PPR on this finds things that transitively use/depend on seeds
        scores = self._ppr(self.adj_reverse, seed_indices)

        for si in seed_indices:
            scores[si] = 0.0

        top_idx = np.argsort(-scores)[:budget]
        return {self.uid_list[i] for i in top_idx if scores[i] > 0}

    def harvest_forward_ppr(self, seeds: set[str], budget: int) -> set[str]:
        """Forward PPR: find things seeds DEPEND ON (callees, downstream)."""
        seed_indices = np.array([self.uid_to_idx[u] for u in seeds if u in self.uid_to_idx])
        if len(seed_indices) == 0:
            return set()

        scores = self._ppr(self.adj_forward, seed_indices)

        for si in seed_indices:
            scores[si] = 0.0

        top_idx = np.argsort(-scores)[:budget]
        return {self.uid_list[i] for i in top_idx if scores[i] > 0}


def main():
    t_start = time.monotonic()
    out = open(OUTPUT, "w")

    def log(msg: str = ""):
        out.write(msg + "\n")
        out.flush()
        print(msg)

    log("=" * 70)
    log("RECALL GAP ANALYSIS (fast, no model inference)")
    log("=" * 70)
    log()

    # Accumulators
    totals = defaultdict(int)
    per_repo: list[dict] = []

    for repo_key, repo_info in REPO_MAP.items():
        gt_prefix = repo_info["gt_prefix"]
        index_path = CLONES_ROOT / repo_key / ".recon" / "index.db"

        if not index_path.exists():
            log(f"  SKIP {repo_key}: no index.db")
            continue

        gt_dirs = sorted([
            d for d in DATA_ROOT.iterdir()
            if d.is_dir() and d.name.startswith(gt_prefix + "_pr")
        ])
        if not gt_dirs:
            continue

        t0 = time.monotonic()
        print(f"[{repo_key}] Loading index...", end="", flush=True)
        idx = RepoIndex(index_path)
        print(f" {len(idx.nodes):,} nodes, loaded in {time.monotonic()-t0:.1f}s")

        repo_stats = defaultdict(int)

        for gt_dir in gt_dirs:
            queries_file = gt_dir / "ground_truth" / "queries.jsonl"
            touched_file = gt_dir / "ground_truth" / "touched_objects.jsonl"
            if not queries_file.exists() or not touched_file.exists():
                continue

            with open(touched_file) as f:
                touched = [json.loads(l) for l in f if l.strip()]
            gt_uids = idx.resolve_gt(touched)
            if len(gt_uids) < 2:
                continue

            with open(queries_file) as f:
                queries = [json.loads(l) for l in f if l.strip()]

            for q in queries:
                if not q.get("seeds") and not q.get("pins"):
                    continue
                seeds = idx.resolve_seeds(q)
                if not seeds:
                    continue
                gt = gt_uids - seeds
                if not gt:
                    continue

                # Simulate each harvester
                graph_found = idx.harvest_graph_1hop(seeds)
                term_found = idx.harvest_term_match(q)
                import_found = idx.harvest_imports(seeds)
                explicit_found = idx.harvest_explicit(seeds)
                splade_qt_found = idx.harvest_splade_query(q["query_text"])

                # Current pool (all existing harvesters)
                current_pool = graph_found | term_found | import_found | explicit_found | splade_qt_found

                # New signals
                budget = max(len(current_pool), 500)
                centroid_found = idx.harvest_splade_centroid(seeds, budget)
                rev_ppr_found = idx.harvest_reverse_ppr(seeds, budget)
                fwd_ppr_found = idx.harvest_forward_ppr(seeds, budget)

                # Union of everything
                all_signals = current_pool | centroid_found | rev_ppr_found | fwd_ppr_found

                # Measure against GT
                pool_hit = current_pool & gt
                centroid_hit = centroid_found & gt
                rev_ppr_hit = rev_ppr_found & gt
                fwd_ppr_hit = fwd_ppr_found & gt
                all_hit = all_signals & gt

                # Incremental: what each new signal adds over current pool
                missed = gt - pool_hit
                centroid_adds = centroid_found & missed
                rev_ppr_adds = rev_ppr_found & missed
                fwd_ppr_adds = fwd_ppr_found & missed

                repo_stats["queries"] += 1
                repo_stats["gt"] += len(gt)
                repo_stats["pool"] += len(pool_hit)
                repo_stats["centroid"] += len(centroid_hit)
                repo_stats["rev_ppr"] += len(rev_ppr_hit)
                repo_stats["fwd_ppr"] += len(fwd_ppr_hit)
                repo_stats["all"] += len(all_hit)
                repo_stats["centroid_adds"] += len(centroid_adds)
                repo_stats["rev_ppr_adds"] += len(rev_ppr_adds)
                repo_stats["fwd_ppr_adds"] += len(fwd_ppr_adds)

        elapsed = time.monotonic() - t0
        nq = repo_stats["queries"]
        gt_total = repo_stats["gt"]
        if nq > 0 and gt_total > 0:
            pool_r = repo_stats["pool"] / gt_total
            all_r = repo_stats["all"] / gt_total
            log(
                f"[{repo_key}] {nq} queries, {elapsed:.1f}s | "
                f"Pool: {pool_r:.1%} | +RevPPR: +{repo_stats['rev_ppr_adds']} | "
                f"+Centroid: +{repo_stats['centroid_adds']} | "
                f"ALL: {all_r:.1%}"
            )

        for k, v in repo_stats.items():
            totals[k] += v
        per_repo.append({"repo": repo_key, **dict(repo_stats)})

    # Summary
    log()
    log("=" * 70)
    log("AGGREGATE RESULTS")
    log("=" * 70)
    gt = totals["gt"]
    pool = totals["pool"]
    log(f"\nTotal queries: {totals['queries']}")
    log(f"Total GT items (excl seeds): {gt:,}")
    log()
    log(f"  Signal                   Found     Recall    Adds over pool")
    log(f"  {'-'*24} {'-'*9} {'-'*9} {'-'*14}")
    log(f"  {'Current pool':<24} {pool:>9,} {pool/max(gt,1):>8.1%}   (baseline)")
    log(f"  {'SPLADE centroid':<24} {totals['centroid']:>9,} {totals['centroid']/max(gt,1):>8.1%}   +{totals['centroid_adds']:,}")
    log(f"  {'Reverse PPR':<24} {totals['rev_ppr']:>9,} {totals['rev_ppr']/max(gt,1):>8.1%}   +{totals['rev_ppr_adds']:,}")
    log(f"  {'Forward PPR':<24} {totals['fwd_ppr']:>9,} {totals['fwd_ppr']/max(gt,1):>8.1%}   +{totals['fwd_ppr_adds']:,}")
    all_total = totals["all"]
    log(f"  {'ALL combined':<24} {all_total:>9,} {all_total/max(gt,1):>8.1%}")
    log()
    missed = gt - pool
    log(f"  Current pool misses: {missed:,} ({missed/max(gt,1):.1%})")
    log(f"  With all new signals: recall {pool/max(gt,1):.1%} → {all_total/max(gt,1):.1%} (+{(all_total-pool)/max(gt,1):.1%}pp)")

    total_elapsed = time.monotonic() - t_start
    log(f"\n  Completed in {total_elapsed:.0f}s")

    out.close()


if __name__ == "__main__":
    main()
