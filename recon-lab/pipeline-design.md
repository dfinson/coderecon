---
title: Training Pipeline Design
description: End-to-end pipeline architecture for training CodeRecon ranking models
---

## Data Inventory

| Repo Set | Repos | PR Instances | Languages | Role |
|----------|-------|-------------|-----------|------|
| ranker-gate | 30 | ~960 | C++, C#, Go, Java, JS/TS, Python, Ruby, Rust, Swift | Train (ranker + gate) |
| cutoff | 48 | ~1,500 | Same mix | Train (cutoff model) |
| eval | 19 | ~420 | Same mix | **Eval only** |
| **Total** | **97** | **~2,880** | **9 languages** | |

### Train/Eval Split

Repos are assigned to exactly one set (`lab.toml`).  The eval set is
held out entirely — no repo appears in both training and eval.

Non-OK queries (UNSAT/BROAD/AMBIG) provide negative examples for gate
classifier training.

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ PHASE 1: clone  (network + disk)                            │
│  • Clone 97 repos (full history)                            │
│  • Stored in clones/{set}/{repo_name}/                      │
│  • No indexing — daemon handles that later                  │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 2: pr-import  (LLM: ~15 calls/instance)              │
│  For each repo:                                             │
│    • Fetch merged PRs from GitHub API                       │
│    • Create git worktree at PR merge commit                 │
│    • Register worktree with daemon → indexes diff           │
│    • Generate 8 OK queries + ground truth via LLM           │
│    • Write task JSON + manifest.json                        │
│                                                             │
│  Indexing happens automatically: daemon register-worktree   │
│  runs git diff main...HEAD, then reindex_incremental()      │
│  into the shared index.db with the worktree overlay.        │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 3: non-ok-queries  (LLM: ~6 calls/instance)          │
│  For each instance with OK queries:                         │
│    • Generate UNSAT, BROAD, AMBIG negative queries          │
│    • Validate against index (fact-check, spread stats)      │
│    • Write non_ok_queries.json                              │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 4: collect  (CPU-bound, no LLM)                       │
│  For each instance:                                         │
│    • Load shared index.db with worktree overlay             │
│    • For each query: run raw_signals_pipeline()             │
│    • Extract 54+ features per candidate                     │
│    • Write candidates_rank.parquet per instance             │
│                                                             │
│  Collect resolves the main repo's .recon/index.db and       │
│  uses the instance worktree as repo_root with               │
│  worktree_name for correct overlay queries.                 │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 5: merge  (I/O bound)                                 │
│  • Combine all per-instance parquets                        │
│  • Join with labels (relevant=1, irrelevant=0)              │
│  • Add repo metadata (object_count, file_count)             │
│  • Output: candidates_rank.parquet (~2-5 GB expected)       │
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
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 7: install + eval                                     │
│  • Copy .lgbm files to src/coderecon/ranking/models/        │
│  • Run eval on held-out eval set (19 repos)                 │
│  • Report NDCG@5/10/20, MAP, MRR                           │
└─────────────────────────────────────────────────────────────┘
```

---

## Execution Plan

### Phase 1: Clone

```bash
recon-lab clone --set all
```

### Phase 2: PR Import

```bash
recon-lab pr-import --set all --workers 8
```

Daemon automatically indexes each worktree on registration.

### Phase 3: Non-OK Queries

```bash
recon-lab non-ok-queries --set all --workers 4
```

### Phase 4: Collect

```bash
recon-lab collect --set ranker-gate --workers 4
recon-lab collect --set cutoff --workers 4
```

### Phase 5-7: Merge → Train → Install

```bash
recon-lab merge
recon-lab train --output-dir ~/.recon/recon-lab/models
cp ~/.recon/recon-lab/models/*_structural.lgbm src/coderecon/ranking/models/
```

### Phase 8: Eval

```bash
recon-lab eval
```

---

## Key Design Decisions

### No separate index phase

The daemon's `register-worktree` command handles indexing natively.
When a worktree is registered, the daemon:
1. Runs `git diff --name-only main...HEAD` to find changed files
2. Calls `reindex_incremental(paths, worktree=name)` into the shared
   `index.db` with the correct `worktree_id`
3. The shared index stores all worktrees via `files.worktree_id` FK

Queries use the overlay: `_search_worktrees = [worktree_name, "main"]`
so worktree-specific files shadow the main branch per-file.

### Collect uses two paths

Each collect worker receives:
- **main_clone_dir**: The main repo clone (e.g., `clones/ranker-gate/fmt/`)
  that owns `.recon/index.db` and `.recon/tantivy/`
- **instance_clone_dir**: The PR worktree (e.g., `clones/instances/cpp-fmt_pr4638/`)
  used as `repo_root` for file reads, with `worktree_name` for overlay

---

## Resource Requirements

| Phase | CPU | RAM | Disk | LLM Calls | Cost |
|-------|-----|-----|------|-----------|------|
| Clone | Low | 2 GB | ~15 GB | 0 | $0 |
| PR Import | Low | 4 GB | ~10 GB | ~43K | ~$7-12 |
| Non-OK Queries | Low | 2 GB | ~1 GB | ~17K | ~$3-5 |
| Collect | High | 8-12 GB | ~5 GB (parquets) | 0 | $0 |
| Merge | Low | 4 GB | ~5 GB | 0 | $0 |
| Train | Medium | 4 GB | ~50 MB | 0 | $0 |
| **Total** | | | **~36 GB** | **~60K** | **~$12-19** |
