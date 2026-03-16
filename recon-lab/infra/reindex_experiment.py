#!/usr/bin/env python3
"""
Re-indexing experiments: test alternative embedding strategies.

Experiments:
A. Current def-aggregated approach (baseline) — uses existing emb_rank from parquet
B. File-level scaffold embedding — embed whole-file scaffolds, not per-def
C. File-level scaffold + enrichment (calls, strings)
D. Hybrid: max(file_emb_sim, best_def_emb_sim) per file
E. Raw file content embedding (truncated to 1800 chars)

All evaluated with max-fusion across queries, filtered GT, 30KB budget.
"""

import json
import os
import re
import sqlite3
import sys
import gc
import time
from pathlib import Path
from collections import defaultdict, Counter
from typing import Any

import numpy as np
import pyarrow.parquet as pq

os.environ.setdefault("ORT_DISABLE_ALL_LOGS", "1")
os.environ.setdefault("ONNXRUNTIME_DISABLE_CUDA", "1")

DATA = Path.home() / ".cpl-lab/data"
CLONES = Path.home() / ".cpl-lab/clones/eval"
K_RRF = 200  # Use K=200 (best from sweep)
BUDGET = 30_000
SCAFFOLD_BUDGET = 1800

REPOS = [
    ("ruby-sinatra", "sinatra"),
    ("go-gin", "gin"),
    ("php-console", "console"),
    ("java-mockito", "mockito"),
]


# ═══════════════════════════════════════════════════════════
# SCAFFOLD BUILDING (adapted from codeplane source)
# ═══════════════════════════════════════════════════════════

_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_PATH_STRIP = re.compile(
    r"^(src|lib|app|internal|pkg|cmd|main|source|sources|include|includes)/"
)


def _word_split(name: str) -> list[str]:
    name = _CAMEL_RE.sub(" ", name)
    return [w.lower() for w in re.split(r"[_\-./\\]+| +", name) if w]


def _path_to_phrase(path: str) -> str:
    p = _PATH_STRIP.sub("", path)
    p = re.sub(r"\.[^.]+$", "", p)  # strip extension
    return " ".join(_word_split(p))


def _compact_sig(name: str, sig: str | None) -> str:
    if not sig:
        return name
    s = sig.strip()
    if s.startswith("("):
        # Remove self/cls/this
        inner = s[1 : s.find(")")] if ")" in s else s[1:]
        params = [p.strip() for p in inner.split(",") if p.strip()]
        params = [p for p in params if p.lower() not in ("self", "cls", "this")]
        return f"{name}({', '.join(params)})"
    return f"{name}{s}"


def build_file_scaffold(
    file_path: str,
    defs: list[dict[str, Any]],
    imports: list[dict[str, Any]],
) -> str:
    """Build a file-level scaffold text for embedding."""
    lines = []
    # Module line
    phrase = _path_to_phrase(file_path)
    if phrase:
        lines.append(f"module {phrase}")

    # Imports
    import_words = set()
    for imp in imports:
        src = imp.get("source_literal") or imp.get("imported_name") or ""
        if src:
            parts = src.rsplit(".", 1)
            import_words.update(_word_split(parts[-1]))
    if import_words:
        lines.append(f"imports {', '.join(sorted(import_words)[:20])}")

    # Defs
    kind_order = {
        "class": 0, "struct": 0, "interface": 0, "trait": 0, "enum": 0,
        "function": 1, "method": 2, "property": 3, "constant": 4, "variable": 5,
    }
    sorted_defs = sorted(
        defs, key=lambda d: (kind_order.get(d.get("kind", ""), 9), d.get("name", ""))
    )
    def_parts = []
    for d in sorted_defs:
        kind = d.get("kind", "")
        name = d.get("name", "")
        sig = d.get("signature_text")
        ret = d.get("return_type")
        if not name:
            continue
        cs = _compact_sig(name, sig)
        part = f"{kind} {cs}" if kind else cs
        if ret:
            part += f" -> {ret}"
        def_parts.append(part)
    if def_parts:
        lines.append(f"defines {', '.join(def_parts[:30])}")

    # Docstrings
    for d in sorted_defs[:5]:
        doc = d.get("docstring", "")
        if doc:
            first = doc.split("\n")[0].strip()[:100]
            if first:
                lines.append(f"describes {d.get('name','')}: {first}")

    return "\n".join(lines)


def build_enriched_scaffold(
    file_path: str,
    defs: list[dict[str, Any]],
    imports: list[dict[str, Any]],
    calls: list[str] | None = None,
    strings: list[str] | None = None,
) -> str:
    """File scaffold + calls + string mentions."""
    base = build_file_scaffold(file_path, defs, imports)
    parts = [base]
    if calls:
        parts.append(f"calls {', '.join(calls[:20])}")
    if strings:
        parts.append(f"mentions {', '.join(strings[:15])}")
    text = "\n".join(parts)
    return text[:SCAFFOLD_BUDGET]


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def f1(sel, gt):
    n = len(gt)
    if n == 0:
        return (0, 0, 0)
    tp = sum(1 for f in sel if f in gt)
    fp = len(sel) - tp
    p = tp / (tp + fp) if (tp + fp) else 0
    r = tp / n if n else 0
    return (p, r, 2 * p * r / (p + r) if (p + r) else 0)


def pick_budget(ranked, sizes, budget=BUDGET):
    sel = []
    b = budget
    for p in ranked:
        c = sizes.get(p, 500)
        if b < c and sel:
            break
        b -= c
        sel.append(p)
    return sel


def load_gt(repo_id, indexed_files):
    gt_data = {}
    for tf in sorted((DATA / repo_id / "ground_truth").glob("*.json")):
        if tf.name in ("summary.json", "non_ok_queries.json"):
            continue
        gt = json.loads(tf.read_text())
        qs = gt.get("queries", [])
        if not isinstance(qs, list):
            continue
        gf = {
            d["path"]
            for d in gt.get("minimum_sufficient_defs", [])
            if isinstance(d, dict) and "path" in d
        } & indexed_files
        if gf and qs:
            gt_data[gt.get("task_id", tf.stem)] = (gf, qs)
    return gt_data


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    from fastembed import TextEmbedding

    print("Loading embedding model...", flush=True)
    model = TextEmbedding(
        model_name="BAAI/bge-small-en-v1.5",
        providers=["CPUExecutionProvider"],
        max_length=512,
    )

    cols = [
        "query_id", "emb_rank", "emb_score", "term_match_count",
        "graph_edge_type", "graph_seed_rank", "import_direction",
        "retriever_hits", "symbol_source", "is_test", "path",
    ]

    all_results = defaultdict(lambda: defaultdict(list))

    for repo_id, clone_name in REPOS:
        print(f"\n{'='*60}\n{repo_id}\n{'='*60}", flush=True)
        clone = CLONES / clone_name
        conn = sqlite3.connect(str(clone / ".codeplane" / "index.db"))

        # ── Gather file data from DB ──
        indexed_files = set()
        scaff_sizes = {}
        file_data = {}  # path -> {defs, imports}

        for fid, fp in conn.execute("SELECT id, path FROM files"):
            indexed_files.add(fp)
            ds = conn.execute(
                "SELECT kind, name, signature_text, return_type, docstring "
                "FROM def_facts WHERE file_id=?", (fid,)
            ).fetchall()
            ims = conn.execute(
                "SELECT imported_name, source_literal "
                "FROM import_facts WHERE file_id=?", (fid,)
            ).fetchall()

            defs = [
                {"kind": k, "name": n, "signature_text": s, "return_type": r, "docstring": doc}
                for k, n, s, r, doc in ds
            ]
            imports = [
                {"imported_name": n, "source_literal": s}
                for n, s in ims
            ]
            file_data[fp] = {"defs": defs, "imports": imports}
            scaff_sizes[fp] = (
                60 + len(fp or "")
                + sum(50 + len(n or "") + len(s or "") for n, s in ims)
                + sum(
                    10 + len(k or "") + len(n or "") + len(sig or "") + len(ret or "") + 20
                    for k, n, sig, ret, _doc in ds
                )
            )
        conn.close()

        # ── Build file scaffolds ──
        print(f"  Building scaffolds for {len(file_data)} files...", flush=True)
        file_paths = sorted(file_data.keys())
        scaffolds_basic = {}
        scaffolds_enriched = {}

        for fp in file_paths:
            fd = file_data[fp]
            scaffolds_basic[fp] = build_file_scaffold(fp, fd["defs"], fd["imports"])
            # For enriched: extract call names from all defs
            all_calls = []
            for d in fd["defs"]:
                # We don't have _sem_facts in the DB, but we have def names
                # which serve as a proxy for what this file "is about"
                pass
            scaffolds_enriched[fp] = build_enriched_scaffold(
                fp, fd["defs"], fd["imports"]
            )

        # ── Read raw file content ──
        print(f"  Reading raw file content...", flush=True)
        raw_content = {}
        for fp in file_paths:
            full = clone / fp
            if full.is_file():
                try:
                    raw_content[fp] = full.read_text(errors="replace")[:SCAFFOLD_BUDGET]
                except Exception:
                    raw_content[fp] = ""
            else:
                raw_content[fp] = ""

        # ── Embed all file scaffolds ──
        print(f"  Embedding basic scaffolds...", flush=True)
        texts_basic = [f"FILE_SCAFFOLD\n{scaffolds_basic[fp]}" if scaffolds_basic[fp] else fp for fp in file_paths]
        t0 = time.time()
        vecs_basic = np.array(list(model.embed(texts_basic, batch_size=32)), dtype=np.float32)
        norms = np.linalg.norm(vecs_basic, axis=1, keepdims=True)
        vecs_basic /= np.where(norms > 0, norms, 1.0)
        print(f"    {len(texts_basic)} embeddings in {time.time()-t0:.1f}s", flush=True)

        # Embed raw content
        print(f"  Embedding raw content...", flush=True)
        texts_raw = [raw_content.get(fp, fp)[:SCAFFOLD_BUDGET] or fp for fp in file_paths]
        t0 = time.time()
        vecs_raw = np.array(list(model.embed(texts_raw, batch_size=32)), dtype=np.float32)
        norms = np.linalg.norm(vecs_raw, axis=1, keepdims=True)
        vecs_raw /= np.where(norms > 0, norms, 1.0)
        print(f"    {len(texts_raw)} embeddings in {time.time()-t0:.1f}s", flush=True)

        # ── Load GT ──
        gt_data = load_gt(repo_id, indexed_files)
        print(f"  Tasks: {len(gt_data)}", flush=True)

        # ── Load existing signals for baseline ──
        pf = pq.ParquetFile(DATA / repo_id / "signals" / "candidates_rank.parquet")
        qf = {}
        for rg in range(pf.metadata.num_row_groups):
            tbl = pf.read_row_group(rg, columns=cols)
            qids = tbl.column("query_id").to_pylist()
            paths = tbl.column("path").to_pylist()
            embs = tbl.column("emb_rank").to_pylist()
            escores = tbl.column("emb_score").to_pylist()
            terms = tbl.column("term_match_count").to_pylist()
            graphs = tbl.column("graph_edge_type").to_pylist()
            gseeds = tbl.column("graph_seed_rank").to_pylist()
            imps = tbl.column("import_direction").to_pylist()
            agrees = tbl.column("retriever_hits").to_pylist()
            syms = tbl.column("symbol_source").to_pylist()
            tests = tbl.column("is_test").to_pylist()
            for i in range(len(qids)):
                qid = qids[i]; path = paths[i]
                if qid not in qf:
                    qf[qid] = {}
                if path not in qf[qid]:
                    qf[qid][path] = [9999, 0, 0, 0, 9999, 0, 0, 0, 0.0]
                d = qf[qid][path]
                d[0] = min(d[0], embs[i] or 9999)
                d[1] = max(d[1], terms[i] or 0)
                d[2] = max(d[2], agrees[i] or 0)
                if graphs[i]: d[3] = 1
                g = gseeds[i] or 0
                if g > 0: d[4] = min(d[4], g)
                if imps[i]: d[5] = 1
                if syms[i]: d[6] = 1
                if tests[i]: d[7] = 1
                d[8] = max(d[8], escores[i] or 0.0)

        # Path to index mapping for embedding lookup
        path_to_idx = {fp: i for i, fp in enumerate(file_paths)}

        # ── Run strategies ──
        strategies = {
            "A_baseline_rrf": defaultdict(lambda: defaultdict(float)),
            "B_file_scaffold_emb": defaultdict(lambda: defaultdict(float)),
            "C_raw_content_emb": defaultdict(lambda: defaultdict(float)),
            "D_file_scaffold_rrf": defaultdict(lambda: defaultdict(float)),
            "E_hybrid_max_emb": defaultdict(lambda: defaultdict(float)),
            "F_file_scaffold_only_rrf": defaultdict(lambda: defaultdict(float)),
        }

        for qid, fd in qf.items():
            tid = qid.rsplit("/", 1)[0]
            if tid not in gt_data:
                continue
            gf, tqs = gt_data[tid]

            # Get query text
            qi = qid.rsplit("/", 1)[-1]
            qi = int(qi[1:]) if qi.startswith("Q") else 0
            if qi >= len(tqs):
                continue
            qo = tqs[qi]
            if not isinstance(qo, dict):
                continue
            qt = qo.get("query_text", "")
            if not qt:
                continue

            paths = list(fd.keys())
            K = K_RRF

            # ── A: Baseline RRF (def-level emb ranks) ──
            erk = {p: r for r, p in enumerate(sorted(paths, key=lambda p: fd[p][0]), 1)}
            trk = {p: r for r, p in enumerate(sorted(paths, key=lambda p: -fd[p][1]), 1)}
            ark = {p: r for r, p in enumerate(sorted(paths, key=lambda p: -fd[p][2]), 1)}
            grk = {p: r for r, p in enumerate(sorted(paths, key=lambda p: fd[p][4]), 1)}
            for p in paths:
                rsc = (1/(K+erk[p]) + 1/(K+trk[p]) + 1/(K+ark[p]) + 1/(K+grk[p])
                       + fd[p][3]*0.005 + fd[p][5]*0.003 + fd[p][6]*0.003 - fd[p][7]*0.005)
                strategies["A_baseline_rrf"][tid][p] = max(
                    strategies["A_baseline_rrf"][tid][p], rsc
                )

            # ── Embed query ──
            q_vec = np.array(list(model.embed([qt], batch_size=1))[0], dtype=np.float32)
            q_vec /= np.linalg.norm(q_vec) or 1.0

            # ── B: File scaffold embedding only ──
            for p in file_paths:
                idx = path_to_idx[p]
                sim = float(q_vec @ vecs_basic[idx])
                strategies["B_file_scaffold_emb"][tid][p] = max(
                    strategies["B_file_scaffold_emb"][tid][p], sim
                )

            # ── C: Raw content embedding only ──
            for p in file_paths:
                idx = path_to_idx[p]
                sim = float(q_vec @ vecs_raw[idx])
                strategies["C_raw_content_emb"][tid][p] = max(
                    strategies["C_raw_content_emb"][tid][p], sim
                )

            # ── D: File scaffold emb as RRF component ──
            # Replace def-level emb_rank with file-level scaffold similarity rank
            file_sims = {p: float(q_vec @ vecs_basic[path_to_idx[p]]) for p in paths if p in path_to_idx}
            # Add files not in candidates but in index
            for p in file_paths:
                if p not in file_sims:
                    file_sims[p] = float(q_vec @ vecs_basic[path_to_idx[p]])

            fsrk = {p: r for r, p in enumerate(sorted(file_sims.keys(), key=lambda p: -file_sims[p]), 1)}
            # For files in candidate set (have other signals): full RRF
            for p in paths:
                if p in fsrk:
                    rsc = (1/(K+fsrk[p]) + 1/(K+trk.get(p,len(paths))) + 1/(K+ark.get(p,len(paths))) + 1/(K+grk.get(p,len(paths)))
                           + fd.get(p,[0]*9)[3]*0.005 + fd.get(p,[0]*9)[5]*0.003
                           + fd.get(p,[0]*9)[6]*0.003 - fd.get(p,[0]*9)[7]*0.005)
                else:
                    rsc = 0
                strategies["D_file_scaffold_rrf"][tid][p] = max(
                    strategies["D_file_scaffold_rrf"][tid][p], rsc
                )
            # Also include files NOT in candidates but high file similarity
            for p in file_paths:
                if p not in paths and p in fsrk:
                    rsc = 1/(K+fsrk[p])  # only file emb signal
                    strategies["D_file_scaffold_rrf"][tid][p] = max(
                        strategies["D_file_scaffold_rrf"][tid][p], rsc
                    )

            # ── E: Hybrid — max(file_scaffold_sim, def_emb_score) per file ──
            for p in paths:
                def_score = fd[p][8]  # best def emb_score for this file
                file_score = file_sims.get(p, 0)
                hybrid = max(def_score, file_score)
                strategies["E_hybrid_max_emb"][tid][p] = max(
                    strategies["E_hybrid_max_emb"][tid][p], hybrid
                )
            # Add non-candidate files via file emb
            for p in file_paths:
                if p not in paths:
                    strategies["E_hybrid_max_emb"][tid][p] = max(
                        strategies["E_hybrid_max_emb"][tid][p], file_sims.get(p, 0)
                    )

            # ── F: File scaffold emb replaces def emb in RRF, ALL files ranked ──
            all_fsrk = {p: r for r, p in enumerate(sorted(file_paths, key=lambda p: -file_sims.get(p, 0)), 1)}
            for p in file_paths:
                has_cand = p in fd
                if has_cand:
                    d = fd[p]
                    rsc = (1/(K+all_fsrk[p]) + 1/(K+trk.get(p,len(paths)+1)) + 1/(K+ark.get(p,len(paths)+1)) + 1/(K+grk.get(p,len(paths)+1))
                           + d[3]*0.005 + d[5]*0.003 + d[6]*0.003 - d[7]*0.005)
                else:
                    rsc = 1/(K+all_fsrk[p])
                strategies["F_file_scaffold_only_rrf"][tid][p] = max(
                    strategies["F_file_scaffold_only_rrf"][tid][p], rsc
                )

        # ── Evaluate ──
        for label, task_scores in strategies.items():
            for tid, ps in task_scores.items():
                gf = gt_data[tid][0]
                ranked = sorted(ps.keys(), key=lambda p: -ps[p])
                all_results[label][repo_id].append(f1(pick_budget(ranked, scaff_sizes), gf))

        # Per-repo summary
        print(f"\n{'Strategy':<28s} {'P':>6s} {'R':>6s} {'F1':>6s}", flush=True)
        print("-" * 50)
        for label in sorted(strategies.keys()):
            vals = all_results[label][repo_id]
            p = np.mean([v[0] for v in vals])
            r = np.mean([v[1] for v in vals])
            f = np.mean([v[2] for v in vals])
            print(f"  {label:<26s} {p:6.1%} {r:6.1%} {f:6.1%}", flush=True)

        del qf, vecs_basic, vecs_raw, strategies
        gc.collect()

    # ── Consolidated table ──
    print(f"\n{'='*80}")
    print(f"CONSOLIDATED F1 (max-fusion, K={K_RRF}, filtered GT)")
    print(f"{'='*80}")
    print(f"{'Strategy':<28s}", end="")
    for r, _ in REPOS:
        print(f" {r:>14s}", end="")
    print(f" {'MEAN':>8s}")
    print("-" * 86)
    for strat in sorted(all_results.keys()):
        print(f"{strat:<28s}", end="")
        f1s = []
        for r, _ in REPOS:
            v = all_results[strat].get(r, [])
            if v:
                m = np.mean([x[2] for x in v])
                f1s.append(m)
                print(f" {m:>13.1%}", end="")
            else:
                print(f" {'N/A':>14s}", end="")
        print(f" {np.mean(f1s):>7.1%}" if f1s else "")

    print(f"\nRECALL:")
    print(f"{'Strategy':<28s}", end="")
    for r, _ in REPOS:
        print(f" {r:>14s}", end="")
    print(f" {'MEAN':>8s}")
    print("-" * 86)
    for strat in sorted(all_results.keys()):
        print(f"{strat:<28s}", end="")
        rs = []
        for r, _ in REPOS:
            v = all_results[strat].get(r, [])
            if v:
                m = np.mean([x[1] for x in v])
                rs.append(m)
                print(f" {m:>13.1%}", end="")
            else:
                print(f" {'N/A':>14s}", end="")
        print(f" {np.mean(rs):>7.1%}" if rs else "")

    print(f"\nPRECISION:")
    print(f"{'Strategy':<28s}", end="")
    for r, _ in REPOS:
        print(f" {r:>14s}", end="")
    print(f" {'MEAN':>8s}")
    print("-" * 86)
    for strat in sorted(all_results.keys()):
        print(f"{strat:<28s}", end="")
        ps = []
        for r, _ in REPOS:
            v = all_results[strat].get(r, [])
            if v:
                m = np.mean([x[0] for x in v])
                ps.append(m)
                print(f" {m:>13.1%}", end="")
            else:
                print(f" {'N/A':>14s}", end="")
        print(f" {np.mean(ps):>7.1%}" if ps else "")


if __name__ == "__main__":
    main()
