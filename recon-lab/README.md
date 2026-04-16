# recon-lab

Training pipeline for CodeRecon's recon models. Produces three LightGBM
models that ship as package data in `src/coderecon/ranking/data/`:

| Model | File | Purpose |
|-------|------|---------|
| Ranker | `ranker.lgbm` | LambdaMART object scorer — P(touched \| query, object) |
| Cutoff | `cutoff.lgbm` | Regressor — predict how many top objects to return |
| Gate | `gate.lgbm` | Multiclass classifier — OK / UNSAT / BROAD / AMBIG |

---

## 1. Foundations

### 1.1 Semantic Object

The unit of prediction is a **DefFact**: a row in the `def_facts` table with a
stable `def_uid`, `kind`, `name`, `lexical_path`, `start_line`, `end_line`,
and file reference.

DefFact kinds across languages:

| Kind | Category | What it is |
|------|----------|-----------|
| `function` | Code | Top-level function |
| `method` | Code | Method inside a class/struct/trait |
| `class` | Code | Class definition |
| `struct` | Code | Struct (Rust, Go, C#) |
| `interface` | Code | Interface (TS, Go, Java, C#) |
| `trait` | Code | Trait (Rust) |
| `enum` | Code | Enum |
| `variable` | Code | Top-level or module-level variable |
| `constant` | Code | Named constant |
| `module` | Code | Module declaration |
| `property` | Code | Property (getter/setter) |
| `pair` | Non-code | Config key-value (TOML/YAML/JSON) |
| `key` | Non-code | Config key |
| `table` | Non-code | Config section (TOML) |
| `target` | Non-code | Build target (Makefile) |
| `heading` | Non-code | Document heading (Markdown) |

### 1.2 Retrieval Signals

Four retrieval sources produce candidates. Each returns its full natural
result set — no artificial caps, no budget limits.

| Signal | Source | Granularity |
|--------|--------|-------------|
| Lexical | Tantivy full-text search | Line hits mapped to containing DefFact |
| Term match | SQL LIKE on DefFact names | Per DefFact |
| Graph | 1-hop structural walk (callees, callers, siblings) | Per DefFact |
| Symbol/Explicit | Direct symbol/path resolution from query text, seeds, and pins | Per DefFact |

Harvesters emit raw measurements only — no pre-scored evidence, no
hardcoded weights. Categorical features (`graph_edge_type`,
`symbol_source`, `import_direction`) replace numeric scores. The ranker
learns signal value from data.

### 1.3 Query Tiers

Seven OK query tiers: four isolation (each exercises a single retrieval
signal) and three combination (exercise multi-signal agreement).
Seeds and pins are separate arguments alongside the query text.

**Isolation tiers** (one signal each):

| Tier | Name | Primary signal | Seeds | Pins |
|------|------|---------------|-------|------|
| **Q_LEXICAL** | Lexical | Full-text / Tantivy | none | none |
| **Q_IDENTIFIER** | Identifier | Term match (SQL LIKE) | none | none |
| **Q_STRUCTURAL** | Structural | Graph (1-hop walk) | 1–2 | none |
| **Q_NAVIGATIONAL** | Navigational | Explicit path/symbol resolution | none | 2–4 |

**Combination tiers** (multi-signal):

| Tier | Name | Signals combined | Seeds | Pins |
|------|------|-----------------|-------|------|
| **Q_LEX_IDENT** | Lexical + Identifier | Full-text + term match | 2–3 | none |
| **Q_IDENT_NAV** | Identifier + Navigational | Term match + explicit | 2–4 | 2–4 |
| **Q_FULL** | Full signal | All signals | 2–4 | 2–4 |

**Non-OK tiers** (up to 4 each, optional — skip if forced):

| Tier | Definition |
|------|------------|
| **UNSAT** | Query makes factually wrong assumptions about the repo |
| **BROAD** | Work spanning 15+ files in 3+ unrelated directories |
| **AMBIG** | 2+ subsystems could be the target; query doesn't specify which |

Total per task: 7 required OK + 0–12 optional non-OK = 7–19 queries.

### 1.4 Gate Taxonomy

Four labels, defined as properties of the **(query, repo) pair**:

| Label | Definition |
|-------|-----------|
| **OK** | The query maps to a specific, bounded neighborhood of semantic objects. |
| **UNSAT** | The query makes factually wrong assumptions about the repo. |
| **BROAD** | The touched set is structurally dispersed — no ranked list with a reasonable cutoff achieves acceptable precision and recall. |
| **AMBIG** | Multiple disjoint neighborhoods could plausibly be the target. |

### 1.5 Relevance Model

Binary labelling. Each definition is either relevant (1) or
irrelevant (0).  Relevant defs are those whose spans overlap a
changed hunk in the gold patch (`minimum_sufficient`).

| Label | Meaning |
|-------|---------|
| 1 | Relevant — definition overlaps a changed hunk |
| 0 | Irrelevant — not relevant to the task |

LambdaMART optimizes NDCG with these binary gains.

---

## 2. Models

### 2.1 Object Ranker (Model 1)

**Goal:** Score $P(\text{relevant} \mid q, o)$ for each candidate DefFact.

**Base model:** LightGBM LambdaMART. Graded relevance gains (2/1/0).
Grouped by `(run_id, query_id)`. Only OK-labeled queries.

**Per-candidate features:**

```
# Identity
def_uid, path, kind, name, lexical_path

# Span
start_line, end_line, object_size_lines

# Path features
file_ext, parent_dir, path_depth

# Structural metadata from index
has_docstring, has_decorators, has_return_type
hub_score, is_test, signature_text, namespace
nesting_depth, has_parent_scope

# Retriever signals (None if retriever didn't find this def)
term_match_count, term_total_matches
lex_hit_count
graph_edge_type, graph_seed_rank
symbol_source
import_direction
retriever_hits
```

### 2.2 Cutoff (Model 2)

**Goal:** Predict $N(q)$: how many top-ranked objects to return.

**Base model:** LightGBM regressor. Target: $N^*(q) = \arg\max_N F_1$.

**Features:** query features + repo features + score distribution
(percentiles, gaps, entropy, variance, cumulative mass) + retriever
agreement distribution.

**Training:** K-fold across all 50 repos. Out-of-fold ranker predictions
→ compute $N^*$ per query → 7,500 rows, no leakage.

### 2.3 Gate (Model 3)

**Goal:** Classify (query, repo) as OK / UNSAT / BROAD / AMBIG.

**Base model:** LightGBM multiclass. Cross-entropy objective.

**Features:** query text features + repo features + retrieval
distribution features (top score, score decay, path entropy, cluster
count, retriever agreement, seed presence).

| Gate output | Action |
|------------|--------|
| OK | Ranker + cutoff → return ranked DefFacts |
| UNSAT | Surface mismatch |
| AMBIG | Ask for disambiguation |
| BROAD | Ask for decomposition |

---

## 3. Runtime Flow

```
Query + Seeds + Pins
  │
  ├─ Lexical search (Tantivy)
  ├─ Term match (SQL LIKE)
  ├─ Symbol/path resolution (seeds + pins + query text)
  │
  ▼
Candidate pool (union by def_uid, per-retriever raw signals)
  │
  ├─ Graph walk (1-hop from candidates with ≥2 signals or explicit)
  │
  ▼
Full candidate pool + features
  │
  ├─ Gate classifies (query, repo)
  │     ├─ UNSAT → surface mismatch
  │     ├─ AMBIG → ask for disambiguation
  │     ├─ BROAD → ask for decomposition
  │     └─ OK ──┐
  │              ▼
  │     Ranker scores each candidate
  │              ▼
  │     Cutoff predicts N(q)
  │              ▼
  │     Return top N ranked DefFacts
  │
  ▼
Response to agent (ranked semantic spans)
```

**Output:** ranked list of DefFacts. No file tiers. No file-level
grouping. No test co-retrieval. Presentation is the consumer's concern.

---

## 4. Dataset Generation

### 4.1 Repo Selection

Three repo sets, 98 repos total:

- **Ranker + Gate** (30 repos): train ranker and gate. 10 languages × 3 repos.
- **Cutoff** (48 repos): train cutoff with disjoint data. Medium-scale repos
  with varied codebase sizes for diverse N* distributions, plus small and
  large scale anchors for N* range coverage.
  No K-fold needed — cutoff repos are scored by the ranker trained on the
  ranker+gate set, so no leakage. Gate also trains on these repos
  (gate uses raw signals, not ranker output, so no leakage).
- **Eval** (20 repos): held-out evaluation. 10 languages × 2 repos each.

All repos generate the same full query set: 7 OK queries + up to 12
non-OK queries per task. Gate uses all queries from all 78 training
repos. Cutoff uses only OK queries from cutoff repos.

**Total:** 98 repos, 3,234 tasks, ~28,000+ queries.

### 4.3 Data Collection Pipeline

Ground truth is now imported from SWE-bench base-commit snapshots and
then adapted into CodeRecon's retrieval schema.

#### SWE-bench stages

Each SWE-bench instance is handled as its own snapshot-backed data unit:

| # | Stage | What it does |
|---|-------|-------------|
| 1 | **Load** | Read SWE-bench rows from Hugging Face datasets |
| 2 | **Assign** | Deterministically route instances into `ranker-gate`, `cutoff`, or `eval` |
| 3 | **Snapshot** | Materialize a worktree at the instance `base_commit` |
| 4 | **Index** | Run `cpl init` on that exact snapshot |
| 5 | **Parse** | Convert `patch` + `test_patch` into changed files and hunks |
| 6 | **Map** | Resolve changed hunks to indexed defs in `.recon/index.db` |
| 7 | **Filter context** | Keep broader read-only defs via cheap LLM relevance checks |
| 8 | **Adapt** | Use an LLM to write task complexity, OK queries, and non-OK queries |
| 9 | **Emit** | Write one GT task plus manifest metadata under `data/{instance_id}/` |

#### Invocation

SWE-bench import is driven from the unified CLI:

```bash
# Import the evaluation set
recon-lab swebench --set eval

# Import one repo's training instances
recon-lab swebench --set ranker-gate --repo django/django --max-instances 25
```

#### Ground truth JSON schema

Each imported instance produces one file: `data/{workspace_id}/ground_truth/{workspace_id}.json`.

```json
{
  "task_id": "django__django_15814",
  "task_complexity": "narrow",
  "task_text": "<problem_statement>",
  "diff": "<patch + test_patch>",
  "solve_notes": "Fixes the Django migration edge case described in the SWE-bench instance.",
  "confidence": "high|medium|low",
  "source": "swebench",
  "source_dataset": "princeton-nlp/SWE-bench",
  "source_split": "dev",
  "source_instance_id": "django__django-15814",
  "source_repo": "django/django",
  "base_commit": "<sha>",
  "logical_repo_id": "django__django",
  "minimum_sufficient_defs": [
    {"path": "...", "name": "...", "kind": "...", "start_line": 42, "end_line": 57, "reason": "changed hunk overlap"}
  ],
  "excluded_defs": [
    {"path": "...", "name": "...", "kind": "...", "start_line": 1, "end_line": 3, "reason": "..."}
  ],
  "queries": [
    {"query_type": "Q_LEXICAL", "query_text": "...", "seeds": [], "pins": [], "justification": "..."}
  ]
}
```

Field details, query type rules, and validation constraints live in
`src/cpl_lab/patch_ground_truth.py`, `src/cpl_lab/swebench_llm.py`, and `src/cpl_lab/validate_ground_truth.py`.

#### Data assembly

1. For each def in `minimum_sufficient_defs`:
   look up `(path, name, kind)` in coderecon index → resolve `def_uid`.
2. If no match: flag for review (should be <2%).
3. Write `touched_objects.jsonl` with `tier` field (`minimum`).
4. Write `audit/` with `diff`, `justification`, `excluded_defs`,
   `confidence`, `solve_notes` for third-agent auditing.
5. Assemble `runs.jsonl`, `queries.jsonl`.

#### GT output

Each imported instance produces:
- `data/{workspace_id}/manifest.json`
- `data/{workspace_id}/ground_truth/{workspace_id}.json`
- `data/{workspace_id}/ground_truth/non_ok_queries.json`
- `data/{workspace_id}/ground_truth/runs.jsonl`
- `data/{workspace_id}/ground_truth/touched_objects.jsonl`
- `data/{workspace_id}/ground_truth/queries.jsonl`
- `data/{workspace_id}/ground_truth/summary.json`

#### Phase 4: Retrieval signal collection (re-runnable)

For each query in ground truth:

```
recon_raw_signals(query=query_text, seeds=seeds, pins=pins)
```

Returns candidate pool with per-retriever raw signals. Seeds/pins
affect the pool: seeded symbols enter via the explicit harvester
(`symbol_source="agent_seed"`), pinned paths inject all defs from
those files (`symbol_source="pin"`).

Join with ground truth → `label_relevant` per candidate (binary).

#### Phase 4b: Test relevance data (collected with ground truth)

During Phase 2, the executor also runs the test suite with coverage
enabled. The executor itself analyzes the coverage results and
identifies which pre-existing test functions cover the changed lines.
The reviewer verifies this analysis.

**What the executor does:**

1. Runs the test suite with coverage against the reverted state
   (pre-change baseline).
2. Reads the coverage report to find which test functions cover the
   lines that were changed in the task commit diff.
3. Excludes any test functions the executor wrote as part of the task
   (new tests are in the diff — they're not pre-existing ground truth).
4. Constructs a single `test_query` describing the full diff:
   `"Find tests that verify the behavior of {symbols} in {files}"`
   where symbols and files come from the diff. One query per task —
   all changed symbols and files in one sentence.
5. Constructs `diff_seeds` (changed symbol names from the diff) and
   `diff_pins` (changed file paths from the diff).
6. Records all of this in the `test_selection` field of the ground
   truth JSON.

**What the reviewer verifies:**

1. `test_query` accurately describes the changed symbols and files.
2. `relevant_preexisting_tests` only contains tests that actually
   exist in the pre-change codebase (not new tests).
3. The `covers_changed_lines` reference real line numbers from the diff.
4. `diff_seeds` and `diff_pins` match the actual diff content.
5. `new_tests_excluded` lists all new test functions from the diff.

**Ground truth labels:**

| Field | What it contains | Source |
|-------|-----------------|--------|
| `test_query` | Natural language query describing the full diff | Executor constructs from diff |
| `relevant_preexisting_tests` | Pre-existing test DefFacts that cover changed lines | Executor analyzes coverage + diff |
| `import_graph_test_files` | Test files that import changed modules | Executor identifies from imports |
| `diff_seeds` | Changed symbol names | Executor extracts from diff |
| `diff_pins` | Changed file paths | Executor extracts from diff |
| `new_tests_excluded` | New test functions written by executor | Executor identifies from diff |

The `relevant_preexisting_tests` set is the ground truth for test
selection. The `import_graph_test_files` set is the baseline (current
checkpoint behavior). The ratio |import_graph| / |relevant| measures
over-selection.

---

## 5. Training Data Schemas

### 5.1 `runs`

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Unique task run identifier |
| `repo_id` | str | Repository identifier |
| `task_id` | str | Task identifier |
| `task_text` | str | Full task description |

### 5.2 `touched_objects`

One row per relevant def per task. Absence = irrelevant.
Binary labelling: `minimum` = relevant (1), absent = irrelevant (0).

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Task run identifier |
| `def_uid` | str | DefFact stable identity |
| `path` | str | File path |
| `kind` | str | DefFact kind |
| `name` | str | DefFact name |
| `start_line` | int | Span start |
| `end_line` | int | Span end |
| `tier` | str | `minimum` |

### 5.3 `queries`

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Task run identifier |
| `query_id` | str | Unique query identifier |
| `query_text` | str | Full query text |
| `query_type` | str | Q_LEXICAL / Q_IDENTIFIER / Q_STRUCTURAL / Q_NAVIGATIONAL / Q_LEX_IDENT / Q_IDENT_NAV / Q_FULL / UNSAT / BROAD / AMBIG |
| `seeds` | list[str] | Symbol names passed as seeds |
| `pins` | list[str] | File paths passed as pins |
| `label_gate` | str | OK / UNSAT / BROAD / AMBIG |

Non-OK queries live in a separate per-repo file:
`data/{repo_id}/non_ok_queries.json`. They are merged into `queries`
during data assembly with `label_gate` set to the query type.

### 5.4 `candidates_rank`

One group per `(run_id, query_id)`. Per-candidate features from
`recon_raw_signals` output + binary relevance label.

### 5.5 `queries_cutoff`

One row per OK query from CUTOFF repos only. Score distribution
features from ranker output (ranker trained on ranker+gate repos)
+ $N^*$ target.

### 5.6 `queries_gate`

One row per query (all types). Retrieval distribution features +
gate label.

---

## 6. Training Procedures

### 6.1 Ranker

1. Filter to OK-labeled queries.
2. Train LightGBM LambdaMART grouped by `(run_id, query_id)`.
3. Binary relevance: 1 (relevant), 0 (irrelevant).

### 6.2 Cutoff (disjoint repo split)

1. Train ranker on 30 ranker+gate repos.
2. Score all 48 cutoff repos with the trained ranker.
3. Compute $N^*$ per cutoff query.
4. Zero leakage (ranker never saw cutoff data).
5. Train cutoff regressor.

### 6.3 Gate

1. All query types (OK + UNSAT + BROAD + AMBIG) from ALL 78 training
   repos (30 ranker+gate + 48 cutoff).
2. Retrieval distribution features from candidate pools.
3. LightGBM multiclass, cross-entropy.

### 6.4 Shipment Sequence

1. Data collection (30 ranker+gate repos, 48 cutoff repos, 20 eval repos)
2. Gate training (all 78 training repos) → gate ships
3. Ranker training (30 ranker+gate repos) → validate NDCG on eval set
4. Cutoff training (48 cutoff repos scored by trained ranker)
5. Full pipeline ships (gate + ranker + cutoff)

---

## 7. Evaluation

Uses the Inspect AI evaluation framework.

**Metrics** (reported per query type):

| Metric | Granularity |
|--------|-------------|
| NDCG of ranked list | Per query |
| F1 / precision / recall of returned set | Per query |
| Gate accuracy + confusion matrix | Per query / aggregate |
| Empty-result rate | Aggregate |
| Returned-set size | Aggregate |

**Comparison:** head-to-head with heuristic recon baseline on F1 delta,
precision delta, recall delta, and latency.

**Agent trace collection:** During eval set runs, export VS Code chat
replays (raw JSON) to `data/{repo_id}/replays/`. The raw export
contains full LLM request metadata (tokens, timing, TTFT, cache hits),
tool call args/responses, conversation context, and agent reasoning.
Run `chatreplay_to_traces.py` post-collection to extract compressed
traces for efficiency metrics (turns, tokens, tool calls). Keep raw
exports as source of truth — they can be re-extracted with richer
formats later.

---

## 8. Design Invariants

Every numeric boundary is either learned from data (ranker scores,
cutoff N, gate boundaries), a system configuration parameter
(rendering budget), or computed empirically per query ($N^*$).

No artificial caps in retrievers. No hardcoded evidence scores. No
arbitrary constants in model definitions, feature computations, or
training procedures. Seeds and pins inject candidates but do not boost
scores — the ranker decides their value. Graph seeds = candidates
found by ≥2 retrievers or explicitly mentioned, no scoring formula.

---

## 9. Project Layout

### In-repo (versioned pipeline source)

```
recon-lab/
├── README.md                   # This file — spec + operations
├── pyproject.toml
├── lab.toml                    # Default pipeline configuration
├── dvc.yaml                   # DVC pipeline DAG (clone → eval)
├── justfile                   # Task runner (just pipeline, just train, etc.)
├── .env.example               # Environment variable documentation
├── repos/                     # 98 task definitions (30 ranker-gate + 48 cutoff + 20 eval)
│   ├── ranker-gate/           #   30 repos — training set for ranker + gate
│   ├── cutoff/                #   48 repos — training set for cutoff
│   └── eval/                  #   20 repos — held-out evaluation set
└── src/cpl_lab/               # Training code + unified CLI
    ├── cli.py                 # Click CLI entry point (recon-lab)
    ├── config.py              # Configuration resolution
    ├── schema.py              # §5 dataset table schemas
    ├── clone.py               # Repo cloning
    ├── index.py               # Indexing (recon init on all clones)
    ├── swebench.py            # SWE-bench ground truth import
    ├── swebench_llm.py        # LLM adaptation for SWE-bench instances
    ├── patch_ground_truth.py  # Diff parsing, hunk-to-def mapping
    ├── collector.py           # Ground truth collection
    ├── collect.py             # Signal collection adapter (§4.3 Phase 4)
    ├── collect_signals.py     # Retrieval signal collection
    ├── merge.py               # Merge adapter
    ├── merge_ground_truth.py  # Merge per-task JSONs into JSONL
    ├── merge_signals.py       # Merge signal data
    ├── train.py               # Training adapter (§6)
    ├── train_ranker.py        # §6.1 LambdaMART training
    ├── train_cutoff.py        # §6.2 no-leakage K-fold cutoff training
    ├── train_gate.py          # §6.3 multiclass gate training
    ├── train_all.py           # Orchestrates all 3 training stages
    ├── evaluate.py            # Inspect AI evaluation integration (§7)
    ├── validate.py            # Ground truth validation
    └── status.py              # Pipeline status dashboard
```

### Runtime inference (ships with coderecon)

```
src/coderecon/ranking/          # Deployed models + inference
├── ranker.py                   # §2.1 LambdaMART inference
├── cutoff.py                   # §2.2 cutoff inference
├── gate.py                     # §2.3 gate inference
├── features.py                 # Feature extraction (§2.1)
├── models.py                   # Model loading
└── data/                       # Serialized .lgbm model artifacts
```

### Inspect AI evaluation (in src/cpl_lab/eval/)

```
eval/
├── tasks.py                    # @task definitions (Inspect AI entry points)
├── run.py                      # Inspect AI evaluation runner
├── datasets/eval_gt.py         # eval_gt_dataset() → Dataset
├── datasets/scaffold_rank.py   # scaffold_rank_dataset() → Dataset
├── models/ranking.py           # @solver ranking_solver()
├── models/llm_reranker.py      # @solver llm_reranker()
├── metrics/ranking.py          # @scorer ranking_scorer() NDCG/Hit@K/Cutoff
└── metrics/gate.py             # @scorer gate_scorer() accuracy/confusion
```

### External workspace (mutable data, outside repo)

```
$CPL_LAB_WORKSPACE/              (default: ~/.recon/recon-lab)
├── clones/                      # Cloned + coderecon-indexed repos
│   ├── ranker-gate/
│   ├── cutoff/
│   └── eval/
├── data/
│   ├── {repo_id}/
│   │   ├── ground_truth/        # Per-task JSONs (intermediate)
│   │   │   ├── PR-*.json        # Raw mined tasks
│   │   │   ├── runs.jsonl
│   │   │   ├── touched_objects.jsonl
│   │   │   └── queries.jsonl
│   │   ├── mine_summary.json    # Per-repo mining stats
│   │   └── signals/             # Retrieval signals per query
│   ├── merged/                  # Training parquets
│   └── logs/
│       ├── sessions/            # Per-session agent transcripts
│       └── errors/              # Archived failed attempt logs
└── .venv/                       # Optional pipeline virtualenv
```

---

## 10. CLI Reference

All pipeline stages are orchestrated via the `recon-lab` CLI or via `just`:

```bash
cd recon-lab

# Clone repos
recon-lab clone --set ranker-gate
recon-lab clone --set all

# Index cloned repos
recon-lab index

# Import SWE-bench ground truth
recon-lab swebench --set ranker-gate
recon-lab swebench --set all --max-instances 100

# Collect retrieval signals (§4.3 Phase 4)
recon-lab collect

# Merge data into training parquets
recon-lab merge

# Train models (§6)
recon-lab train --model all
recon-lab train --model ranker

# Evaluate with Inspect AI (§7)
recon-lab eval

# Validate ground truth
recon-lab validate

# Check pipeline status
recon-lab status
```

Or use the task runner:

```bash
just pipeline        # Run full DVC pipeline (dvc repro)
just pipeline-to train  # Run up to train stage
just eval            # Run Inspect AI evaluation
just pipeline-dag    # Show pipeline DAG
just pipeline-status # Show what's changed
```

---

## 11. Setup

```bash
cd recon-lab

# Install dependencies (creates .venv)
uv sync

# Copy and configure environment (optional — for Azure Foundry LLM)
cp .env.example .env
# Edit .env with endpoint from: cd ../infra && terraform output

# Workspace directory is created automatically by the CLI at ~/.recon/recon-lab
# Override via lab.toml [workspace] path or --workspace flag
```

## 12. Training workflow (end-to-end)

```bash
just pipeline   # or: dvc repro
```

Individual stages:

1. **Clone** — `recon-lab clone --set all`
2. **Index** — `recon-lab index --set all`
3. **Ground truth** — `recon-lab swebench --set all` (SWE-bench import + LLM query adaptation)
4. **Signals** — `recon-lab collect` runs `raw_signals_pipeline()` per query
5. **Merge** — `recon-lab merge` assembles training parquets
6. **Train** — `recon-lab train --model all` runs gate → ranker → cutoff (§6)
7. **Eval** — `recon-lab eval` runs Inspect AI evaluation against held-out set
8. **Deploy** — copy `*.lgbm` into `src/coderecon/ranking/data/`

## 13. Infrastructure

The pipeline uses Azure AI Foundry for LLM calls during query generation (stage 3).
Terraform config lives in `../infra/`:

```bash
just infra-plan   # Preview changes
just infra-apply  # Provision: rg-coderecon-lab + AI Services + gpt-4.1-mini
just infra-output # Show endpoint URL for .env
```

Alternatively, the pipeline works with GitHub Models (no infra needed) — see `.env.example`.

## 14. Archived Artifacts

The original LLM-generated ground truth data (pre-SWE-bench pivot) was removed from
the working tree. It is recoverable from git history:

```bash
git show f4a0f30:recon-lab/gt-backup/ranking-data-backup-20260311-111812.zip > old-gt.zip
```

This data was abandoned after verification showed only 13–46% of LLM-labeled defs
matched real AST spans. The current pipeline uses SWE-bench patch-based labels instead.
