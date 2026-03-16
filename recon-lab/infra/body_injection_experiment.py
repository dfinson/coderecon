"""
Re-embed eval repos with section body injection for non-code defs,
then re-run evaluation comparing old vs new embedding quality.

This script:
1. Loads defs from each eval repo's index.db
2. For non-code defs, reads source files to extract section body text
3. Builds new scaffolds with body injection (production code)
4. Re-embeds with fastembed (same model: bge-small-en-v1.5)
5. Queries against eval queries to measure emb_rank changes
6. Computes full RRF evaluation to measure F1 impact
"""
import json, sqlite3, sys, time
import numpy as np
import pandas as pd
from pathlib import Path

# Use production scaffold builder
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from codeplane.index._internal.indexing.def_embedding import (
    build_def_scaffold, _CODE_KINDS, _DEF_SCAFFOLD_CHAR_BUDGET,
)

DATA = Path.home() / '.cpl-lab/data'
CLONES = Path.home() / '.cpl-lab/clones/eval'
REPOS = [('ruby-sinatra', 'sinatra'), ('go-gin', 'gin'),
         ('php-console', 'console'), ('java-mockito', 'mockito')]

NON_CODE_EXTS = {'.md', '.yml', '.yaml', '.json', '.toml', '.properties', '.txt',
                 '.cfg', '.ini', '.env', '.lock', '.xml', '.html', '.css'}

_NON_CODE_KINDS = {"heading", "pair", "key", "table", "target", "variable"}

K_RRF = 200


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_defs_from_db(db_path, clone_path):
    """Load all defs from index.db, injecting body text for non-code defs."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT d.def_uid, d.kind, d.name, d.lexical_path, "
        "d.signature_text, d.docstring, f.path, d.start_line, d.end_line "
        "FROM def_facts d JOIN files f ON d.file_id = f.id"
    ).fetchall()
    conn.close()

    by_file = {}
    for r in rows:
        path = r[6]
        by_file.setdefault(path, []).append({
            "def_uid": r[0], "kind": r[1], "name": r[2],
            "lexical_path": r[3], "signature_text": r[4], "docstring": r[5],
            "start_line": r[7], "end_line": r[8],
        })

    # Inject body text for non-code defs
    body_injected = 0
    for path, defs in by_file.items():
        needs = any(d["kind"] in _NON_CODE_KINDS for d in defs)
        if not needs:
            continue
        full = clone_path / path
        if not full.exists():
            continue
        try:
            lines = full.read_text(encoding="utf-8", errors="replace").split("\n")
        except Exception:
            continue
        for d in defs:
            if d["kind"] in _NON_CODE_KINDS:
                sl, el = d.get("start_line", 0), d.get("end_line", 0)
                if sl > 0 and el >= sl:
                    body = "\n".join(lines[sl - 1 : el])
                    if body:
                        d["_body_text"] = body
                        body_injected += 1

    return by_file, body_injected


def build_scaffolds(by_file, with_body=True):
    """Build scaffolds for all defs. If with_body=False, strip _body_text."""
    uid_to_scaffold = {}
    uid_to_path = {}
    for path, defs in by_file.items():
        for d in defs:
            uid = d["def_uid"]
            if not with_body:
                d_copy = {k: v for k, v in d.items() if k != "_body_text"}
                scaffold = build_def_scaffold(path, d_copy)
            else:
                scaffold = build_def_scaffold(path, d)
            if scaffold:
                uid_to_scaffold[uid] = scaffold
                uid_to_path[uid] = path
    return uid_to_scaffold, uid_to_path


def embed_scaffolds(scaffolds, model):
    """Embed all scaffolds, return uid->vector mapping."""
    uids = list(scaffolds.keys())
    texts = [scaffolds[uid] for uid in uids]

    log(f"  Embedding {len(texts)} scaffolds...")
    # Sort by length for efficient batching
    idx_sorted = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    texts_sorted = [texts[i] for i in idx_sorted]

    batch_size = 64
    all_vecs = []
    for i in range(0, len(texts_sorted), batch_size):
        batch = texts_sorted[i:i+batch_size]
        vecs = list(model.embed(batch))
        all_vecs.extend(vecs)

    # Unsort
    vecs_unsorted = [None] * len(all_vecs)
    for orig_idx, vec in zip(idx_sorted, all_vecs):
        vecs_unsorted[orig_idx] = vec

    matrix = np.array(vecs_unsorted, dtype=np.float32)
    # Normalize
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms

    return {uid: matrix[i] for i, uid in enumerate(uids)}, matrix, uids


def query_embeddings(query_text, uid_vecs, uids, matrix, model):
    """Query the embedding index, return sorted (uid, score) pairs."""
    q_vec = list(model.embed([query_text]))[0].astype(np.float32)
    q_vec = q_vec / (np.linalg.norm(q_vec) + 1e-10)
    scores = matrix @ q_vec
    ranked = sorted(zip(uids, scores), key=lambda x: -x[1])
    return ranked


def compute_f1(selected, gt):
    if not gt: return 0, 0, 0
    tp = len(selected & gt)
    p = tp / len(selected) if selected else 0
    r = tp / len(gt)
    f1 = 2*p*r/(p+r) if (p+r) > 0 else 0
    return p, r, f1


def main():
    from fastembed import TextEmbedding
    model = TextEmbedding("BAAI/bge-small-en-v1.5", max_length=512)
    log("Model loaded")

    # Two variants to compare
    results = {"baseline": {}, "body_injection": {}}

    for repo_id, short_name in REPOS:
        clone = CLONES / short_name
        db_path = clone / '.codeplane' / 'index.db'
        pq_path = DATA / repo_id / 'signals' / 'candidates_rank.parquet'
        gt_dir = DATA / repo_id / 'ground_truth'

        if not db_path.exists() or not pq_path.exists():
            log(f"SKIP {repo_id}")
            continue

        log(f"\n{'='*60}")
        log(f"REPO: {repo_id}")
        log(f"{'='*60}")

        # Load defs with body text
        by_file, n_body = load_defs_from_db(db_path, clone)
        total_defs = sum(len(v) for v in by_file.values())
        nc_defs = sum(1 for defs in by_file.values() for d in defs if d["kind"] in _NON_CODE_KINDS)
        log(f"  Defs: {total_defs} total, {nc_defs} non-code, {n_body} with body text")

        # Show some example scaffolds
        log(f"\n  --- Example non-code scaffolds (before vs after) ---")
        shown = 0
        for path, defs in by_file.items():
            for d in defs:
                if d["kind"] in _NON_CODE_KINDS and d.get("_body_text") and shown < 3:
                    old = build_def_scaffold(path, {k: v for k, v in d.items() if k != "_body_text"})
                    new = build_def_scaffold(path, d)
                    log(f"\n  OLD ({len(old)} chars): {old[:120]}...")
                    log(f"  NEW ({len(new)} chars): {new[:200]}...")
                    shown += 1

        # Build both scaffold sets
        scaffolds_old, paths_old = build_scaffolds(by_file, with_body=False)
        scaffolds_new, paths_new = build_scaffolds(by_file, with_body=True)
        log(f"  Scaffolds: {len(scaffolds_old)} baseline, {len(scaffolds_new)} with body")

        # Check scaffold size changes
        old_nc_sizes = [len(scaffolds_old[u]) for u in scaffolds_old if paths_old[u].rsplit('.', 1)[-1] in {'md', 'yml', 'yaml', 'json', 'toml', 'properties'}]
        new_nc_sizes = [len(scaffolds_new[u]) for u in scaffolds_new if paths_new[u].rsplit('.', 1)[-1] in {'md', 'yml', 'yaml', 'json', 'toml', 'properties'}]
        if old_nc_sizes:
            log(f"  Non-code scaffold sizes: old median={np.median(old_nc_sizes):.0f}, new median={np.median(new_nc_sizes):.0f}")

        # Embed both
        log(f"\n  Embedding BASELINE...")
        uid_vecs_old, matrix_old, uids_old = embed_scaffolds(scaffolds_old, model)
        log(f"  Embedding BODY INJECTION...")
        uid_vecs_new, matrix_new, uids_new = embed_scaffolds(scaffolds_new, model)

        # Load GT
        gt_by_task = {}
        for f in sorted(gt_dir.glob('*.json')):
            if f.name in ('summary.json', 'non_ok_queries.json'): continue
            gt = json.loads(f.read_text())
            files = {d['path'] for d in gt.get('minimum_sufficient_defs', [])
                     if isinstance(d, dict) and 'path' in d}
            gt_by_task[f"{repo_id}/{f.stem}"] = files

        # Get indexed files
        conn = sqlite3.connect(str(db_path))
        indexed_files = {r[0] for r in conn.execute("SELECT path FROM files").fetchall()}
        conn.close()
        for tid in gt_by_task:
            gt_by_task[tid] &= indexed_files

        # Load parquet for queries and other signals
        df = pd.read_parquet(pq_path)

        # For each query: replace emb_rank/emb_score with new embeddings, compute file-level RRF
        query_results = {"baseline": [], "body_injection": []}
        nc_recall = {"baseline": {"hit": 0, "total": 0}, "body_injection": {"hit": 0, "total": 0}}

        queries_done = 0
        for query_id, qdf in df.groupby('query_id'):
            parts = query_id.rsplit('/', 1)
            task_id = parts[0] if len(parts) == 2 else query_id.rsplit('/Q', 1)[0]
            if task_id not in gt_by_task or not gt_by_task[task_id]:
                continue
            gt_files = gt_by_task[task_id]

            # Get the query text
            # query_id format: repo_id/TASK_ID/Q0 — need actual query text
            # It's not in the parquet. Use the GT file.
            gt_file = gt_dir / f"{task_id.split('/')[-1]}.json"
            if not gt_file.exists():
                continue
            gt_data = json.loads(gt_file.read_text())
            queries = gt_data.get("queries", [])
            q_idx_str = query_id.rsplit("/Q", 1)[-1] if "/Q" in query_id else "0"
            try:
                q_idx = int(q_idx_str)
            except ValueError:
                continue
            if q_idx >= len(queries):
                continue
            query_text = queries[q_idx].get("query_text", "") if isinstance(queries[q_idx], dict) else str(queries[q_idx])
            if not query_text:
                continue

            # Compute new emb ranks for both variants
            for variant, uid_vecs_v, matrix_v, uids_v in [
                ("baseline", uid_vecs_old, matrix_old, uids_old),
                ("body_injection", uid_vecs_new, matrix_new, uids_new),
            ]:
                ranked = query_embeddings(query_text, uid_vecs_v, uids_v, matrix_v, model)

                # Convert def-level ranks to file-level: best def rank per file
                file_best_rank = {}
                file_best_score = {}
                paths_map = paths_old if variant == "baseline" else paths_new
                for rank, (uid, score) in enumerate(ranked, 1):
                    fpath = paths_map.get(uid, "")
                    if fpath and fpath not in file_best_rank:
                        file_best_rank[fpath] = rank
                        file_best_score[fpath] = float(score)

                # File-level aggregation from parquet (non-emb signals)
                fa = qdf.groupby('path').agg(
                    max_term=('term_match_count', 'max'),
                    max_agree=('retriever_hits', 'max'),
                    has_graph=('graph_edge_type', lambda x: x.notna().any()),
                    has_import=('import_direction', lambda x: x.notna().any()),
                    has_sym=('symbol_source', lambda x: x.notna().any()),
                    is_test=('is_test', 'first'),
                ).reset_index()

                # Merge new emb ranks
                fa['emb_r'] = fa['path'].map(file_best_rank).fillna(len(ranked) + 1)
                fa['term_r'] = fa['max_term'].rank(ascending=False, method='min').fillna(len(fa))
                fa['agree_r'] = fa['max_agree'].rank(ascending=False, method='min')
                fa['graph_r'] = (~fa['has_graph']).astype(int) * len(fa) + 1

                # Pure RRF — NO bonuses (constant-free)
                fa['rrf_score'] = (
                    1/(K_RRF + fa['emb_r']) +
                    1/(K_RRF + fa['term_r']) +
                    1/(K_RRF + fa['agree_r']) +
                    1/(K_RRF + fa['graph_r'])
                )

                top5 = set(fa.nlargest(5, 'rrf_score')['path'])
                _, _, f1 = compute_f1(top5, gt_files)
                query_results[variant].append(f1)

                # Track non-code recall
                for gt_path in gt_files:
                    ext = '.' + gt_path.rsplit('.', 1)[-1] if '.' in gt_path else ''
                    if ext.lower() in NON_CODE_EXTS:
                        nc_recall[variant]["total"] += 1
                        if gt_path in top5:
                            nc_recall[variant]["hit"] += 1

            queries_done += 1

        for variant in ["baseline", "body_injection"]:
            f1s = query_results[variant]
            mean_f1 = np.mean(f1s) if f1s else 0
            nc = nc_recall[variant]
            nc_r = nc["hit"] / nc["total"] if nc["total"] > 0 else 0
            results[variant][repo_id] = {
                "mean_f1": mean_f1,
                "nc_recall": nc_r,
                "nc_hit": nc["hit"],
                "nc_total": nc["total"],
                "n_queries": len(f1s),
            }
            log(f"  {variant:20s}: F1={100*mean_f1:.1f}%  NC_recall={100*nc_r:.1f}% ({nc['hit']}/{nc['total']})")

    # Summary
    log(f"\n{'='*70}")
    log(f"SUMMARY: SECTION BODY INJECTION IMPACT")
    log(f"{'='*70}")

    print(f"\n{'Variant':<22s}", end="")
    for repo_id, _ in REPOS:
        short = repo_id.split('-')[1][:6]
        print(f"{'F1 '+short:>10s}", end=" ")
    print(f"{'MEAN F1':>10s} {'NC Recall':>10s}")
    print("-" * 75)

    for variant in ["baseline", "body_injection"]:
        print(f"{variant:<22s}", end="")
        all_f1 = []
        all_nc_hit = 0
        all_nc_total = 0
        for repo_id, _ in REPOS:
            if repo_id in results[variant]:
                r = results[variant][repo_id]
                all_f1.append(r["mean_f1"])
                all_nc_hit += r["nc_hit"]
                all_nc_total += r["nc_total"]
                print(f"{100*r['mean_f1']:>9.1f}%", end=" ")
            else:
                print(f"{'N/A':>10s}", end=" ")
        mean = np.mean(all_f1) if all_f1 else 0
        nc_r = all_nc_hit / all_nc_total if all_nc_total > 0 else 0
        print(f"{100*mean:>9.1f}% {100*nc_r:>9.1f}%")

    print()
    delta_f1 = np.mean([results["body_injection"][r]["mean_f1"] for r in results["body_injection"]]) - \
               np.mean([results["baseline"][r]["mean_f1"] for r in results["baseline"]])
    delta_nc = (sum(results["body_injection"][r]["nc_hit"] for r in results["body_injection"]) /
                max(sum(results["body_injection"][r]["nc_total"] for r in results["body_injection"]), 1)) - \
               (sum(results["baseline"][r]["nc_hit"] for r in results["baseline"]) /
                max(sum(results["baseline"][r]["nc_total"] for r in results["baseline"]), 1))
    print(f"  Delta F1:        {100*delta_f1:+.1f}pp")
    print(f"  Delta NC recall: {100*delta_nc:+.1f}pp")


if __name__ == "__main__":
    main()
