"""
Compare: emb vs RRF vs cross-encoder (GTE reranker ONNX)
File-level F1 within 30KB scaffold budget.
Optimized: streams parquet in batches, no full DataFrame in memory.
"""
import json, sqlite3, time, gc
import numpy as np
import pyarrow.parquet as pq
import onnxruntime as ort
from tokenizers import Tokenizer
from pathlib import Path

DATA = Path.home() / ".cpl-lab/data"
CLONES = Path.home() / ".cpl-lab/clones/eval"
MODEL_DIR = Path.home() / "models/gte-reranker"
BUDGET = 30_000
K_RRF = 60
TOP_N_CE = 20

import sys
_only = sys.argv[1] if len(sys.argv) > 1 else None
ALL_REPOS = [
    ("ruby-sinatra", "sinatra"),
    ("go-gin", "gin"),
    ("php-console", "console"),
    ("java-mockito", "mockito"),
]
REPOS = [(r,c) for r,c in ALL_REPOS if not _only or r == _only]

print("Loading model...", end=" ", flush=True)
tok = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
tok.enable_truncation(max_length=512)
tok.enable_padding(pad_id=0, pad_token="[PAD]")
providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
sess = ort.InferenceSession(str(MODEL_DIR / "model_int8.onnx"), providers=providers)
actual = sess.get_providers()
print(f"done. Providers: {actual}", flush=True)

def ce_score(query, scaffolds):
    if not scaffolds: return np.array([])
    # Process in batches of 5 to limit memory
    all_scores = []
    for i in range(0, len(scaffolds), 5):
        batch = scaffolds[i:i+5]
        encs = tok.encode_batch([(query, s) for s in batch])
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        logits = sess.run(None, {"input_ids": ids, "attention_mask": mask})[0]
        all_scores.extend((1.0 / (1.0 + np.exp(-logits.flatten()))).tolist())
    return np.array(all_scores)

def pick(ranked, sizes):
    budget = BUDGET; sel = []
    for p in ranked:
        c = sizes.get(p, 500)
        if budget < c and sel: break
        budget -= c; sel.append(p)
    return sel

def f1(sel, gt, n):
    tp = sum(1 for f in sel if f in gt)
    fp = len(sel) - tp
    p = tp/(tp+fp) if (tp+fp) else 0
    r = tp/n if n else 0
    return (p, r, 2*p*r/(p+r) if (p+r) else 0)

for repo_id, clone_name in REPOS:
    t0 = time.time()
    clone = CLONES / clone_name
    conn = sqlite3.connect(str(clone / ".codeplane" / "index.db"))

    # Scaffold sizes
    file_ids = {}; scaff = {}
    for fid, fp in conn.execute("SELECT id, path FROM files"):
        file_ids[fp] = fid
        ds = conn.execute("SELECT kind,name,signature_text,return_type FROM def_facts WHERE file_id=?", (fid,)).fetchall()
        ims = conn.execute("SELECT imported_name,source_literal FROM import_facts WHERE file_id=?", (fid,)).fetchall()
        scaff[fp] = 60+len(fp or "")+sum(50+len(n or "")+len(s or "") for n,s in ims)+sum(10+len(k or "")+len(n or "")+len(sig or "")+len(ret or "")+20 for k,n,sig,ret in ds)

    def build_scaff(fp):
        fid = file_ids.get(fp)
        if not fid: return f"# {fp}"
        ds = conn.execute("SELECT kind,name,signature_text,return_type,start_line,end_line FROM def_facts WHERE file_id=? ORDER BY start_line", (fid,)).fetchall()
        ims = conn.execute("SELECT imported_name,source_literal FROM import_facts WHERE file_id=?", (fid,)).fetchall()
        out = [f"# {fp}"]
        for n, s in ims: out.append(f"import {n or ''} from {s or ''}")
        for k, n, sig, ret, sl, el in ds: out.append(f"{k} {n}({sig or ''}) -> {ret or ''} [{sl}-{el}]")
        return "\n".join(out)

    # GT
    gt_data = {}
    for tf in sorted((DATA/repo_id/"ground_truth").glob("*.json")):
        if tf.name in ("summary.json","non_ok_queries.json"): continue
        gt = json.loads(tf.read_text())
        qs = gt.get("queries",[])
        if not isinstance(qs, list): continue
        gf = {d["path"] for d in gt.get("minimum_sufficient_defs",[]) if isinstance(d,dict) and "path" in d}
        if gf and qs: gt_data[gt.get("task_id",tf.stem)] = (gf, qs)

    # Stream parquet — aggregate to file level per query
    print(f"\n{repo_id}: loading...", flush=True)
    pf = pq.ParquetFile(DATA/repo_id/"signals"/"candidates_rank.parquet")
    qf = {}  # qid -> {path -> signals}
    cols = ["query_id","emb_rank","term_match_count","graph_edge_type","graph_seed_rank","import_direction","retriever_hits","symbol_source","is_test","path"]

    for rg in range(pf.metadata.num_row_groups):
        tbl = pf.read_row_group(rg, columns=cols)
        qids = tbl.column("query_id").to_pylist()
        paths = tbl.column("path").to_pylist()
        embs = tbl.column("emb_rank").to_pylist()
        terms = tbl.column("term_match_count").to_pylist()
        graphs = tbl.column("graph_edge_type").to_pylist()
        gseeds = tbl.column("graph_seed_rank").to_pylist()
        imps = tbl.column("import_direction").to_pylist()
        agrees = tbl.column("retriever_hits").to_pylist()
        syms = tbl.column("symbol_source").to_pylist()
        tests = tbl.column("is_test").to_pylist()

        for i in range(len(qids)):
            qid = qids[i]; path = paths[i]
            if qid not in qf: qf[qid] = {}
            if path not in qf[qid]: qf[qid][path] = [9999,0,0,0,9999,0,0,0]
            d = qf[qid][path]
            d[0] = min(d[0], embs[i] or 9999)       # best emb
            d[1] = max(d[1], terms[i] or 0)          # max term
            d[2] = max(d[2], agrees[i] or 0)         # max agree
            if graphs[i]: d[3] = 1                   # has graph
            g = gseeds[i] or 0
            if g > 0: d[4] = min(d[4], g)            # best gseed
            if imps[i]: d[5] = 1                     # has import
            if syms[i]: d[6] = 1                     # has sym
            if tests[i]: d[7] = 1                    # is test

        if (rg+1) % 3 == 0 or rg == pf.metadata.num_row_groups-1:
            print(f"  rg {rg+1}/{pf.metadata.num_row_groups}, {len(qf)} queries", flush=True)

    dt = time.time()-t0
    print(f"  loaded in {dt:.0f}s, evaluating...", flush=True)

    em, rm, cm = [], [], []
    done = 0
    for qid, fd in qf.items():
        tid = qid.rsplit("/",1)[0]
        if tid not in gt_data: continue
        gf, tqs = gt_data[tid]
        qi = qid.rsplit("/",1)[-1]
        qi = int(qi[1:]) if qi.startswith("Q") else 0
        if qi >= len(tqs): continue
        qo = tqs[qi]
        if not isinstance(qo,dict): continue
        qt = qo.get("query_text","")
        if not qt: continue

        paths = list(fd.keys()); n_pos = len(gf)
        done += 1
        if done % 50 == 0: print(f"  {done} queries done", flush=True)

        # Emb
        er = sorted(paths, key=lambda p: fd[p][0])
        em.append(f1(pick(er, scaff), gf, n_pos))

        # RRF
        erk = {p:r for r,p in enumerate(sorted(paths, key=lambda p: fd[p][0]),1)}
        trk = {p:r for r,p in enumerate(sorted(paths, key=lambda p: -fd[p][1]),1)}
        ark = {p:r for r,p in enumerate(sorted(paths, key=lambda p: -fd[p][2]),1)}
        grk = {p:r for r,p in enumerate(sorted(paths, key=lambda p: fd[p][4]),1)}
        rsc = {}
        for p in paths:
            rsc[p] = 1/(K_RRF+erk[p])+1/(K_RRF+trk[p])+1/(K_RRF+ark[p])+1/(K_RRF+grk[p])+fd[p][3]*0.005+fd[p][5]*0.003+fd[p][6]*0.003-fd[p][7]*0.005
        rr = sorted(paths, key=lambda p: -rsc[p])
        rm.append(f1(pick(rr, scaff), gf, n_pos))

        # CE top-50 by RRF
        top = rr[:TOP_N_CE]
        scs = [build_scaff(p) for p in top]
        sc = ce_score(qt, scs)
        cr = [top[i] for i in np.argsort(-sc)]
        cm.append(f1(pick(cr, scaff), gf, n_pos))

    conn.close(); elapsed = time.time()-t0
    print(f"\n{repo_id} — {done} queries ({elapsed:.0f}s):")
    for l, m in [("emb_rank",em),("RRF",rm),("CE reranker",cm)]:
        if m: print(f"  {l:15s}  P={np.mean([x[0] for x in m]):.1%}  R={np.mean([x[1] for x in m]):.1%}  F1={np.mean([x[2] for x in m]):.1%}")
    del qf; gc.collect()

print(f"\ngrep F1 (known): sinatra=14.9%, gin=18.2%, console=15.7%, mockito=12.4%")
