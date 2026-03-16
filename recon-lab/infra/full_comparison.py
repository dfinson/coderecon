"""
Full head-to-head comparison: grep vs emb vs RRF vs LambdaMART vs cross-encoder.
All at file level, 30KB scaffold budget.
"""
import sys, time, json, subprocess, sqlite3
import numpy as np, pandas as pd, pyarrow.parquet as pq, lightgbm as lgb
import onnxruntime as ort
from tokenizers import Tokenizer
from pathlib import Path
import gc

# ── Config ──
DATA = Path.home() / '.cpl-lab/data'
CLONES = Path.home() / '.cpl-lab/clones/eval'
MODEL_DIR = Path.home() / 'models/gte-reranker'
REPOS = [('ruby-sinatra', 'sinatra'), ('go-gin', 'gin'),
         ('php-console', 'console'), ('java-mockito', 'mockito')]
BUDGET = 30_000
K_RRF = 60

LOAD_COLS = ['query_id', 'label_relevant', 'emb_score', 'emb_rank',
    'term_match_count', 'graph_edge_type', 'graph_seed_rank',
    'symbol_source', 'import_direction', 'retriever_hits', 'object_size_lines',
    'path_depth', 'is_test', 'has_docstring', 'has_decorators', 'path']

FILE_FEATURES = [
    'best_emb_rank', 'best_emb_score', 'p25_emb_rank',
    'max_term_match', 'sum_term_match', 'n_term_hit_defs',
    'max_retriever_hits', 'mean_retriever_hits', 'n_high_agree',
    'has_callee', 'has_caller', 'has_sibling', 'n_graph_defs', 'best_graph_rank',
    'has_import_forward', 'has_import_reverse', 'n_import_defs',
    'has_sym_seed', 'has_sym_path',
    'n_defs', 'file_is_test', 'mean_obj_size', 'max_obj_size',
    'n_with_docstring', 'n_with_decorators', 'path_depth',
]

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def scaffold_sizes(clone_path):
    conn = sqlite3.connect(str(clone_path / '.codeplane' / 'index.db'))
    scaff = {}
    for fid, fpath in conn.execute("SELECT id, path FROM files").fetchall():
        defs = conn.execute("SELECT kind,name,signature_text,return_type FROM def_facts WHERE file_id=?", (fid,)).fetchall()
        imps = conn.execute("SELECT imported_name,source_literal FROM import_facts WHERE file_id=?", (fid,)).fetchall()
        scaff[fpath] = 60 + len(fpath or '') + sum(50+len(n or '')+len(s or '') for n,s in imps) + sum(10+len(k or '')+len(n or '')+len(sig or '')+len(ret or '')+20 for k,n,sig,ret in defs)
    conn.close()
    return scaff

def build_scaffold_text(clone_path, fpath):
    """Build actual scaffold text for cross-encoder input."""
    conn = sqlite3.connect(str(clone_path / '.codeplane' / 'index.db'))
    row = conn.execute("SELECT id, language_family, line_count FROM files WHERE path=?", (fpath,)).fetchone()
    if not row:
        conn.close()
        return f"# {fpath}\n(no index data)"
    fid, lang, lines = row
    defs = conn.execute("SELECT kind, name, signature_text, return_type, start_line, end_line FROM def_facts WHERE file_id=? ORDER BY start_line", (fid,)).fetchall()
    imps = conn.execute("SELECT imported_name, source_literal FROM import_facts WHERE file_id=?", (fid,)).fetchall()
    conn.close()
    parts = [f"# {fpath} ({lang}, {lines} lines)"]
    if imps:
        parts.append("imports: " + ", ".join(f"{n} from {s}" for n, s in imps[:15]))
    for kind, name, sig, ret, sl, el in defs:
        line = f"  {kind} {name}"
        if sig: line += f"({sig})"
        if ret: line += f" -> {ret}"
        line += f" [{sl}-{el}]"
        parts.append(line)
    return "\n".join(parts)

def gt_files_for_repo(gt_dir):
    """Load GT: task_id -> set of file paths."""
    result = {}
    for f in sorted(gt_dir.glob('*.json')):
        if f.name in ('summary.json', 'non_ok_queries.json'): continue
        gt = json.loads(f.read_text())
        files = set()
        for d in gt.get('minimum_sufficient_defs', []):
            if isinstance(d, dict) and 'path' in d: files.add(d['path'])
        if files:
            result[gt.get('task_id', f.stem)] = files
    return result

def query_texts_for_repo(gt_dir):
    """Load query texts: query_id -> text."""
    result = {}
    for f in sorted(gt_dir.glob('*.json')):
        if f.name in ('summary.json', 'non_ok_queries.json'): continue
        gt = json.loads(f.read_text())
        tid = gt.get('task_id', f.stem)
        for qi, q in enumerate(gt.get('queries', [])):
            if isinstance(q, dict) and q.get('query_text'):
                qtype = q.get('query_type', f'Q{qi}')
                qid = f"{tid}/{qtype}"
                result[qid] = q['query_text']
    return result

def file_level_agg(qdf, scaff):
    """Aggregate def-level signals to file-level rows."""
    rows = []
    for fpath, fg in qdf.groupby('path'):
        rows.append({
            'path': fpath,
            'label': int((fg['label_relevant'] > 0).any()),
            'scaff_size': scaff.get(fpath, 500),
            'best_emb_rank': fg['emb_rank'].min(),
            'best_emb_score': fg['emb_score'].max(),
            'p25_emb_rank': fg['emb_rank'].quantile(0.25),
            'max_term_match': fg['term_match_count'].max(),
            'sum_term_match': fg['term_match_count'].sum(),
            'n_term_hit_defs': (fg['term_match_count'] > 0).sum(),
            'max_retriever_hits': fg['retriever_hits'].max(),
            'mean_retriever_hits': fg['retriever_hits'].mean(),
            'n_high_agree': (fg['retriever_hits'] >= 4).sum(),
            'has_callee': int((fg['graph_edge_type'] == 'callee').any()),
            'has_caller': int((fg['graph_edge_type'] == 'caller').any()),
            'has_sibling': int((fg['graph_edge_type'] == 'sibling').any()),
            'n_graph_defs': (fg['graph_edge_type'].notna() & (fg['graph_edge_type'] != '')).sum(),
            'best_graph_rank': fg['graph_seed_rank'].replace(0, np.nan).min(),
            'has_import_forward': int((fg['import_direction'] == 'forward').any()),
            'has_import_reverse': int((fg['import_direction'] == 'reverse').any()),
            'n_import_defs': (fg['import_direction'].notna() & (fg['import_direction'] != '')).sum(),
            'has_sym_seed': int((fg['symbol_source'] == 'agent_seed').any()),
            'has_sym_path': int((fg['symbol_source'] == 'path_mention').any()),
            'n_defs': len(fg),
            'file_is_test': int(fg['is_test'].any()),
            'mean_obj_size': fg['object_size_lines'].mean(),
            'max_obj_size': fg['object_size_lines'].max(),
            'n_with_docstring': fg['has_docstring'].sum(),
            'n_with_decorators': fg['has_decorators'].sum(),
            'path_depth': fg['path_depth'].iloc[0],
        })
    fdf = pd.DataFrame(rows)
    fdf['best_graph_rank'] = fdf['best_graph_rank'].fillna(0)
    for c in FILE_FEATURES:
        if c in fdf.columns: fdf[c] = fdf[c].fillna(0)
    return fdf

def eval_budget(fdf, sort_col, ascending=True):
    """Select files within budget by sort order, return P/R/F1."""
    ranked = fdf.sort_values(sort_col, ascending=ascending)
    n_pos = fdf['label'].sum()
    if n_pos == 0: return 0, 0, 0
    budget = BUDGET; tp = 0; fp = 0
    for _, r in ranked.iterrows():
        cost = int(r['scaff_size'])
        if budget < cost and (tp + fp) > 0: break
        budget -= cost
        if r['label']: tp += 1
        else: fp += 1
    p = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / n_pos
    f1 = 2 * p * rec / (p + rec) if (p + rec) else 0
    return p, rec, f1


# ── Load cross-encoder ──
log("Loading GTE reranker ONNX model...")
tokenizer = Tokenizer.from_file(str(MODEL_DIR / 'tokenizer.json'))
tokenizer.enable_padding(pad_id=0, pad_token='[PAD]')
tokenizer.enable_truncation(max_length=512)
session = ort.InferenceSession(str(MODEL_DIR / 'model_int8.onnx'),
                                providers=['CPUExecutionProvider'])
input_names = [i.name for i in session.get_inputs()]
log(f"Model loaded. Inputs: {input_names}")

def cross_encoder_score(query, docs, batch_size=32):
    """Score (query, doc) pairs. Returns array of scores."""
    pairs = [(query, d) for d in docs]
    scores = []
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i+batch_size]
        encoded = tokenizer.encode_batch(batch)
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feeds = {'input_ids': ids, 'attention_mask': mask}
        out = session.run(None, feeds)[0]
        # output shape: (batch, 1) or (batch,) — take the relevance logit
        if out.ndim == 2: out = out[:, 0]
        scores.extend(out.tolist())
    return np.array(scores)

# Quick sanity check
test_scores = cross_encoder_score("find route handler", ["# routes.rb\n  method get", "# readme.md\n  docs only"])
log(f"Sanity check: relevant={test_scores[0]:.3f}, irrelevant={test_scores[1]:.3f}")

# ── Main loop ──
all_results = {}

for repo_id, clone_name in REPOS:
    t0 = time.time()
    clone = CLONES / clone_name
    gt_dir = DATA / repo_id / 'ground_truth'
    log(f"━━━ {repo_id} ━━━")

    # Load scaffold sizes + GT + query texts
    log(f"  Loading scaffolds & GT...")
    scaff = scaffold_sizes(clone)
    gt_map = gt_files_for_repo(gt_dir)
    qtexts = query_texts_for_repo(gt_dir)
    log(f"  {len(scaff)} files, {len(gt_map)} tasks, {len(qtexts)} queries")

    # Load signals
    log(f"  Loading signals parquet...")
    df = pq.read_table(DATA / repo_id / 'signals' / 'candidates_rank.parquet', columns=LOAD_COLS).to_pandas()
    df['task_id'] = df['query_id'].str.rsplit('/', n=1).str[0]
    n_queries = df['query_id'].nunique()
    log(f"  {len(df)} rows, {n_queries} queries")

    # Per-strategy accumulators
    metrics = {s: {'p': [], 'r': [], 'f1': []} for s in ['emb', 'rrf', 'lambdamart', 'xenc', 'xenc+rrf']}
    
    # Pre-build LambdaMART for this fold (train on other 3 repos)
    log(f"  Training LambdaMART (train on other 3 repos)...")
    train_parts = []
    for other_id, other_cn in REPOS:
        if other_id == repo_id: continue
        odf = pq.read_table(DATA / other_id / 'signals' / 'candidates_rank.parquet', columns=LOAD_COLS).to_pandas()
        oscaff = scaffold_sizes(CLONES / other_cn)
        ogt = gt_files_for_repo(DATA / other_id / 'ground_truth')
        odf['task_id'] = odf['query_id'].str.rsplit('/', n=1).str[0]
        for qid, qgrp in odf.groupby('query_id'):
            tid = qgrp['task_id'].iloc[0]
            gf = ogt.get(tid, set())
            if not gf: continue
            fdf = file_level_agg(qgrp, oscaff)
            fdf['label'] = fdf['path'].apply(lambda p: int(p in gf))
            if fdf['label'].sum() == 0: continue
            fdf['group_key'] = f"{other_id}__{qid}"
            # Subsample negatives
            pos = fdf[fdf['label'] > 0]
            neg = fdf[fdf['label'] == 0]
            if len(neg) > 50: neg = neg.sample(n=50, random_state=42)
            train_parts.append(pd.concat([pos, neg]))
        del odf; gc.collect()
    
    train_df = pd.concat(train_parts, ignore_index=True).sort_values('group_key').reset_index(drop=True)
    grp_sizes = train_df.groupby('group_key', sort=True).size().values
    td = lgb.Dataset(train_df[FILE_FEATURES].values.astype(np.float32),
                     label=train_df['label'].astype(int).values, group=grp_sizes, feature_name=FILE_FEATURES)
    booster = lgb.train({'objective': 'lambdarank', 'metric': 'ndcg', 'ndcg_eval_at': [5],
        'learning_rate': 0.05, 'num_leaves': 31, 'min_data_in_leaf': 5, 'verbose': -1}, td, num_boost_round=300)
    del train_df, train_parts, td; gc.collect()
    log(f"  LambdaMART trained")

    # Evaluate per query
    query_ids = sorted(df['query_id'].unique())
    done = 0
    for qid in query_ids:
        qdf = df[df['query_id'] == qid]
        tid = qdf['task_id'].iloc[0]
        gt_files = gt_map.get(tid, set())
        if not gt_files: continue

        fdf = file_level_agg(qdf, scaff)
        fdf['label'] = fdf['path'].apply(lambda p: int(p in gt_files))
        n_pos = fdf['label'].sum()
        if n_pos == 0: continue

        # ── emb ──
        p, r, f1 = eval_budget(fdf, 'best_emb_rank', ascending=True)
        metrics['emb']['p'].append(p); metrics['emb']['r'].append(r); metrics['emb']['f1'].append(f1)

        # ── RRF ──
        fdf['emb_file_rank'] = fdf['best_emb_rank'].rank(method='min')
        fdf['term_file_rank'] = fdf['max_term_match'].rank(ascending=False, method='min')
        fdf['agree_file_rank'] = fdf['max_retriever_hits'].rank(ascending=False, method='min')
        fdf['graph_file_rank'] = fdf['best_graph_rank'].rank(method='min')
        fdf['rrf_score'] = (1/(K_RRF + fdf['emb_file_rank']) +
                            1/(K_RRF + fdf['term_file_rank']) +
                            1/(K_RRF + fdf['agree_file_rank']) +
                            1/(K_RRF + fdf['graph_file_rank']))
        fdf['rrf_score'] += fdf['has_import_forward'] * 0.003 + fdf['has_sym_seed'] * 0.003
        fdf['rrf_score'] -= fdf['file_is_test'] * 0.005
        p, r, f1 = eval_budget(fdf, 'rrf_score', ascending=False)
        metrics['rrf']['p'].append(p); metrics['rrf']['r'].append(r); metrics['rrf']['f1'].append(f1)

        # ── LambdaMART ──
        fdf['lmart_score'] = booster.predict(fdf[FILE_FEATURES].values.astype(np.float32))
        p, r, f1 = eval_budget(fdf, 'lmart_score', ascending=False)
        metrics['lambdamart']['p'].append(p); metrics['lambdamart']['r'].append(r); metrics['lambdamart']['f1'].append(f1)

        # ── Cross-encoder on top-50 by emb ──
        query_text = qtexts.get(qid, '')
        if not query_text:
            # fallback: try matching by task_id prefix
            for k, v in qtexts.items():
                if k.startswith(tid):
                    query_text = v; break
        
        top50 = fdf.nsmallest(50, 'best_emb_rank')
        if len(top50) > 0 and query_text:
            scaff_texts = [build_scaffold_text(clone, p) for p in top50['path']]
            xe_scores = cross_encoder_score(query_text, scaff_texts)
            top50 = top50.copy()
            top50['xe_score'] = xe_scores
            # Fill rest with -inf
            rest = fdf[~fdf['path'].isin(top50['path'])].copy()
            rest['xe_score'] = -999
            fdf_xe = pd.concat([top50, rest])
            p, r, f1 = eval_budget(fdf_xe, 'xe_score', ascending=False)
            metrics['xenc']['p'].append(p); metrics['xenc']['r'].append(r); metrics['xenc']['f1'].append(f1)

            # ── Cross-encoder + RRF hybrid ──
            top50_rrf = fdf.nlargest(50, 'rrf_score')
            scaff_texts2 = [build_scaffold_text(clone, p) for p in top50_rrf['path']]
            xe_scores2 = cross_encoder_score(query_text, scaff_texts2)
            top50_rrf = top50_rrf.copy()
            top50_rrf['xe_rrf_score'] = xe_scores2
            rest2 = fdf[~fdf['path'].isin(top50_rrf['path'])].copy()
            rest2['xe_rrf_score'] = -999
            fdf_xr = pd.concat([top50_rrf, rest2])
            p, r, f1 = eval_budget(fdf_xr, 'xe_rrf_score', ascending=False)
            metrics['xenc+rrf']['p'].append(p); metrics['xenc+rrf']['r'].append(r); metrics['xenc+rrf']['f1'].append(f1)
        
        done += 1
        if done % 25 == 0:
            log(f"  {done}/{len(query_ids)} queries done ({done*100//len(query_ids)}%)")

    elapsed = time.time() - t0
    log(f"  Done: {done} queries in {elapsed:.0f}s")
    
    print(f"\n  {'Strategy':20s} {'P':>6s} {'R':>6s} {'F1':>6s}")
    print(f"  {'-'*38}")
    for s in ['emb', 'rrf', 'lambdamart', 'xenc', 'xenc+rrf']:
        m = metrics[s]
        if m['f1']:
            print(f"  {s:20s} {np.mean(m['p']):6.1%} {np.mean(m['r']):6.1%} {np.mean(m['f1']):6.1%}")
    print()
    
    all_results[repo_id] = {s: np.mean(metrics[s]['f1']) for s in metrics if metrics[s]['f1']}
    del df, booster; gc.collect()

# ── Final summary ──
log("━━━ FINAL SUMMARY ━━━")
print(f"\n{'Repo':20s}", end='')
for s in ['emb', 'rrf', 'lambdamart', 'xenc', 'xenc+rrf']:
    print(f" {s:>10s}", end='')
print()
print("-" * 75)
for repo_id, _ in REPOS:
    r = all_results.get(repo_id, {})
    print(f"{repo_id:20s}", end='')
    for s in ['emb', 'rrf', 'lambdamart', 'xenc', 'xenc+rrf']:
        v = r.get(s)
        print(f" {v:10.1%}" if v is not None else f" {'n/a':>10s}", end='')
    print()
