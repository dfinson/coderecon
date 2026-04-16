# Training Pipeline Design

## Data Inventory

| Dataset | HF ID | Split | Instances | Repos | Language | Role |
|---------|-------|-------|-----------|-------|----------|------|
| SWE-bench | `princeton-nlp/SWE-bench` | `dev` | 225 | 6 | Python | Train |
| SWE-bench | `princeton-nlp/SWE-bench` | `test` | 2,294 | 12 | Python | Train (minus Verified) |
| SWE-bench Verified | `princeton-nlp/SWE-bench_Verified` | `test` | 500 | 12 | Python | **Eval only** |
| Multi-SWE-bench Rust | `r1v3r/multi_SWE_Bench_Rust` | `train` | 239 | 10 | Rust | Train |
| Multi-SWE-bench Java | `Daoguang/Multi-SWE-bench` | `java_verified` | 91 | 6 | Java | Train |

### Train/Eval Split

All 500 Verified instances are a subset of SWE-bench `test`. Training on
raw `test` and evaluating on Verified would be **data leakage**.

Correct partition (verified via `select_instances` test):

- **Train**: test\Verified (1,794) + dev (225) + Rust (239) + Java (91) = **2,349 instances, 28 repos, 3 languages**
- **Eval**: Verified (500 Python instances)
- **Total**: 2,849 instances

Within training, the `cutoff_mod=5` hash split gives:

- ranker-gate: **1,905** instances
- cutoff: **444** instances

108 dev instances already have LLM-generated queries (GT) from a previous
run — the import phase will skip them (`raw_instance.json` + `queries.json`
exist). This saves ~1,620 LLM calls. The non-OK queries (UNSAT/BROAD/AMBIG)
provide negative examples for gate classifier training.

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ PHASE 0: Config                                             │
│  lab.toml changes + eval-exclusion logic in select_instances│
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 1: swebench-import  (LLM: 15 calls/instance)         │
│  For each dataset:                                          │
│    • Load HF dataset                                        │
│    • Clone repo at base_commit (git worktree)               │
│    • Generate 8 OK queries + 6 non-OK queries via LLM      │
│    • Write raw_instance.json + queries.json                 │
│                                                             │
│  ≈ 2,349 × 15 = 35,235 LLM calls (GPT-4.1-mini)           │
│  Est. cost: ~$5-10 at 4.1-mini pricing                     │
│  Est. time: ~4-8 hours (rate-limited)                       │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 2: index  (CPU-bound, no LLM)                         │
│  For each worktree:                                         │
│    • recon init → tree-sitter parse + resolution            │
│    • Produces .recon/index.db per instance                  │
│                                                             │
│  28 base repos → 2,349 worktrees                            │
│  With 1MB file size limit: avg 30s-2min/instance            │
│  Est. time: ~20-40 hours on 1 worker (local)                │
│  With 4 workers: ~5-10 hours                                │
│  On AML D4s_v3: similar (4 vCPU)                            │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 3: swebench-resolve  (index lookup, no LLM)           │
│  For each instance with GT:                                 │
│    • Parse gold patch → file_diffs                          │
│    • map_hunks_to_defs(file_diffs, index.db)                │
│    • Write {workspace_id}.json with labeled defs            │
│                                                             │
│  ≈ 2,349 × ~3-5 LLM calls                                  │
│  Est. cost: ~$2-5                                           │
│  Est. time: ~2-4 hours                                      │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 4: collect  (CPU-bound, no LLM)                       │
│  For each instance:                                         │
│    • Load index.db + ground truth                           │
│    • For each query: run raw_signals_pipeline()             │
│    • Extract 54+ features per candidate                     │
│    • Write candidates_rank.parquet per repo                 │
│                                                             │
│  ≈ 2,349 instances × ~15 queries each                       │
│  Est. time: ~10-20 hours on 2 workers                       │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 5: merge  (I/O bound)                                 │
│  • Combine all per-repo parquets                            │
│  • Join with labels (relevant=1, irrelevant=0)              │
│  • Add repo metadata (object_count, file_count)             │
│  • Output: candidates_rank.parquet (~2-5 GB expected)       │
│                                                             │
│  Est. time: ~5-10 minutes                                   │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 6: train  (CPU, fast)                                 │
│  • 4 LightGBM models (structural-only):                     │
│    - def_ranker (LambdaMART, 500 rounds)                    │
│    - file_ranker (LambdaMART, 500 rounds)                   │
│    - gate (multiclass, 300 rounds)                          │
│    - cutoff (regression, 300 rounds)                        │
│  • Data: ranker-gate set for ranker+gate, cutoff set        │
│  • Subsampling: all positives + ≤50 negatives per query     │
│                                                             │
│  Est. time: <5 minutes                                      │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 7: install + eval                                     │
│  • Copy .lgbm files to src/coderecon/ranking/models/        │
│  • Run eval on SWE-bench Verified (500 instances)           │
│  • Report NDCG@5/10/20, MAP, MRR                           │
└─────────────────────────────────────────────────────────────┘
```

---

## Code Changes (DONE)

1. **Eval-exclusion** in `select_instances()` — always loads eval IDs
   and excludes them from training, plus deduplicates across datasets.
   `swebench_common.py`: new `_iter_training()` helper.
2. **`lab.toml`** updated: `training_split = "test"`.
3. **Plumbed through** `cli.py` → `swebench.py` / `swebench_import.py`.
4. **Old data cleaned**: `recon-lab/data/` (128 MB REPO_MANIFEST GT),
   `repos/` (59 markdown task defs), `experiments/` all deleted.
   `~/.recon/recon-lab/` cleaned: stale worktrees (41 GB), merged
   parquets (539 MB), models (15 MB) removed. Kept: 8 git mirrors
   (3.9 GB), 108 dev GT dirs (4.5 MB).

---

## Execution Plan

### Phase 1: Import (~4-6 hours wallclock)

```bash
recon-lab swebench-import --set all
```

2,349 training + 500 eval = 2,849 instances total.
108 dev instances already imported → skip (~1,620 LLM calls saved).
Remaining: ~2,741 × 15 = ~41K LLM calls (GPT-4.1-mini).
Can parallelize with `--repo` flag per-repo in parallel terminals.

### Phase 2: Index (~10 hours with 4 workers)

```bash
recon-lab index --set all --workers 4
```

The 1MB file size limit (`_MAX_FILE_BYTES`) prevents tree-sitter from
choking on data files.

### Phase 3: Resolve (~3 hours)

```bash
recon-lab swebench-resolve --set ranker-gate
recon-lab swebench-resolve --set cutoff
```

### Phase 4: Collect (~15 hours with 2 workers)

```bash
recon-lab collect --set ranker-gate --workers 2
recon-lab collect --set cutoff --workers 2
```

### Phase 5-7: Merge → Train → Install (~10 minutes)

```bash
recon-lab merge
recon-lab train --output-dir ~/.recon/recon-lab/models
# Copy models to package
cp ~/.recon/recon-lab/models/*_structural.lgbm src/coderecon/ranking/models/
```

### Phase 8: Eval

```bash
recon-lab swebench-import --set eval
recon-lab index --set eval
recon-lab swebench-resolve --set eval
recon-lab eval
```

---

## Resource Requirements

| Phase | CPU | RAM | Disk | LLM Calls | Cost |
|-------|-----|-----|------|-----------|------|
| Import | Low | 2 GB | ~5 GB (queries) | ~41K | ~$7-12 |
| Index | High (all cores) | 8-12 GB | ~50 GB (indexes) | 0 | $0 |
| Resolve | Low | 4 GB | ~1 GB | ~8K | ~$3 |
| Collect | High | 8-12 GB | ~5 GB (parquets) | 0 | $0 |
| Merge | Low | 4 GB | ~5 GB | 0 | $0 |
| Train | Medium | 4 GB | ~50 MB | 0 | $0 |
| **Total** | | | **~66 GB** | **~49K** | **~$12-17** |

Everything runs locally on the 16 GB machine. Indexing is the bottleneck
but feasible with 2-4 workers (CODERECON_INDEX_WORKERS caps internal
parallelism). AML is available as a fallback if local indexing is too slow.

---

## Risk Mitigation

1. **Memory pressure during indexing**: Use `CODERECON_INDEX_WORKERS=4`
   with 2 outer workers to stay under 12 GB. Monitor with `free -h`.

2. **LLM rate limits**: GPT-4.1-mini has generous limits. If throttled,
   use `--repo` to serialize by repo and add retry/backoff.

3. **Disk space**: 2,849 worktrees × git objects are shared per base repo
   (mirror + worktrees). ~50 GB for indexes. 590 GB free on disk.

4. **Phantom GT**: `train_all.py` already drops all-negative query groups
   (defs that don't exist at the checked-out commit). This handles any
   residual GT mapping failures.

5. **Language imbalance**: 86% Python, 10% Rust, 4% Java. LightGBM
   features are language-agnostic (structural, graph, term-match signals).
   No language feature needed — the model learns from structural patterns
   that generalize across languages.
