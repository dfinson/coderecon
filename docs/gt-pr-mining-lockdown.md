# Design Lockdown: PR-Mining Ground Truth Pipeline

**Status:** Locked  
**Date:** 2026-03-18  
**Replaces:** AI-orchestrated GT pipeline (`gt_orchestrator.py`, 6-stage Copilot SDK flow)

---

## 1. Decision

Replace the AI-driven ground truth generation pipeline (`cpl-lab generate`) with
a PR-mining pipeline (`cpl-lab mine`) that extracts ground truth from real merged
GitHub pull requests.

**Rationale:** The current pipeline costs thousands of dollars in AI credits per
full run (Claude Opus × 6 stages × 33 tasks × 90+ repos). The new pipeline
produces equivalent-quality E-file ground truth for ~$12 total, and is
deterministic and auditable.

## 2. What Changes

| Aspect | Old (`generate`) | New (`mine`) |
|--------|-----------------|--------------|
| GT source | AI agent explores codebase | Merged PR diffs |
| E-file accuracy | ~95% (AI judgment) | **100%** (human actually changed them) |
| C-file source | AI judgment | Index-derived (import graph + test files) + cheap filter |
| Cost per repo | ~$50-200 (Opus) | ~$0.15 (Haiku/GPT-4o-mini filter) |
| Total cost (90 repos) | ~$5,000-15,000 | **~$12** |
| Determinism | Non-deterministic | Deterministic (given same PRs) |
| Scale | ~33 tasks/repo | **~50-200 tasks/repo** (all qualifying PRs) |

## 3. What Does NOT Change

The downstream pipeline is untouched:
- `collector.py` — post-processes GT JSON → JSONL tables (same schema)
- `collect_signals.py` — runs queries through `raw_signals_pipeline`
- `merge_ground_truth.py` — merges to Parquet
- `schema.py` — `Run`, `TouchedObject`, `Query`, `CandidateRank` dataclasses
- `train*.py`, `evaluate.py` — model training and evaluation

## 4. Pipeline Design

```
cpl-lab mine [--repo ID] [--set SET] [--max-prs N] [--no-filter]

  ┌─────────────────────────────────────────────────────────┐
  │ For each repo in manifest:                              │
  │                                                         │
  │ 1. FETCH  — gh pr list (merged, with linked issues)     │
  │ 2. FILTER — single-purpose, clean diff, has tests       │
  │ 3. PARSE  — unified diff → (path, hunk_ranges)          │
  │ 4. MAP    — hunk_ranges × def_facts → min_suff defs     │
  │ 5. ENRICH — import graph → thrash_preventing candidates │
  │ 6. FILTER — cheap LLM confirms relevance (optional)     │
  │ 7. QUERY  — issue text → query variants                 │
  │ 8. EMIT   — write {task_id}.json (same schema)          │
  └─────────────────────────────────────────────────────────┘
```

### 4.1 Step 1: Fetch PRs

Source: GitHub API via `gh` CLI.  
For each repo, extract `(owner, name)` from `REPO_MANIFEST` URLs.

```
gh pr list --repo {owner}/{name} --state merged --limit {max_prs} \
  --json number,title,body,files,additions,deletions,mergeCommit,closingIssuesReferences
```

### 4.2 Step 2: Filter PRs

Accept a PR if:
- Has at least one linked/closing issue (i.e., `closingIssuesReferences` non-empty)
- Diff touches ≤ 20 files (rejects bulk refactors)
- Diff has ≥ 1 source file change (not docs-only)
- Issue body is ≥ 50 characters (meaningful description)

### 4.3 Step 3: Parse Diffs

Parse the unified diff to extract `(file_path, [(start_line, end_line)])` per file.
Use `gh pr diff --repo {owner}/{name} {pr_number}` to get the full diff.

### 4.4 Step 4: Map to Definitions

Cross-reference changed line ranges against `def_facts` in the repo's
`.recon/index.db`:

```sql
SELECT d.name, d.kind, d.start_line, d.end_line, f.path
FROM def_facts d
JOIN files f ON d.file_id = f.id
WHERE f.path = :path
  AND d.start_line <= :hunk_end
  AND d.end_line >= :hunk_start
```

Defs overlapping a changed hunk = **minimum_sufficient** (the developer
literally modified them to solve the issue).

### 4.5 Step 5: Enrich with Context Defs

For each minimum_sufficient def, find **thrash_preventing** candidates:

1. **Same-file defs**: Other defs in the same file that weren't changed
   (developer had to read them for context).
2. **Test file defs**: Defs in corresponding test files (conventional
   mapping: `src/foo.py` → `tests/test_foo.py`).
3. **Import dependents** (future): Defs that import from the changed
   module, found via the index's reference/import tables.

### 4.6 Step 6: Cheap LLM Filter (Optional)

For repos where precision matters, pass each thrash_preventing candidate
through a cheap model (Haiku, GPT-4o-mini):

```
Issue: "{issue_title}"
Changed def: {name} in {path}
Candidate context def: {candidate_name} in {candidate_path}
Would a developer need to READ this def to understand how to make the change?
Answer: YES or NO
```

~1K tokens/call × ~20 candidates/task × 100 tasks/repo × 90 repos = ~180M tokens.  
At $0.25/1M tokens (Haiku input): **~$12 total.**

Pass `--no-filter` to skip this step entirely (pure static analysis).

### 4.7 Step 7: Generate Query Variants

From the issue text, generate the required query types:

| Query Type | Source | Method |
|-----------|--------|--------|
| `Q_SEMANTIC` | Issue body first paragraph | Verbatim extract |
| `Q_IDENTIFIER` | Diff changed def names | Extract identifiers |
| `Q_LEXICAL` | Issue title + key terms | Term extraction |
| `Q_NAVIGATIONAL` | Changed file paths | Path-based query |
| `Q_FULL` | Full issue body | Truncated to 500 chars |
| `Q_SEM_IDENT` | Semantic + identifiers | Merge |

Seeds: def names from minimum_sufficient. Pins: file paths from diff.

### 4.8 Step 8: Emit Task JSON

Output matches the existing schema exactly:

```json
{
  "task_id": "PR-{number}",
  "task_complexity": "narrow|medium|wide",
  "task_text": "{issue_body}",
  "diff": "{pr_diff}",
  "solve_notes": "PR #{number}: {pr_title}",
  "confidence": "high",
  "minimum_sufficient_defs": [...],
  "thrash_preventing_defs": [...],
  "tier_difference_reasoning": "minimum_sufficient = defs overlapping changed hunks; thrash_preventing = same-file context + test defs",
  "excluded_defs": [...],
  "queries": [...]
}
```

## 5. Complexity Classification

Derived from diff statistics:
- **narrow**: 1-2 changed files, ≤ 3 minimum_sufficient defs
- **medium**: 3-5 changed files, 4-8 minimum_sufficient defs
- **wide**: 6+ changed files or 9+ minimum_sufficient defs

## 6. Tier Mapping (min_suff vs thrash_prev)

The two-tier model maps directly from PR structure:

- **minimum_sufficient (gain=2)**: Defs whose source lines overlap with
  changed hunks. Removing these from context makes the task unsolvable —
  the developer literally had to edit them.

- **thrash_preventing (gain=1)**: Defs the developer had to *read* but
  not *edit*: same-file context, test patterns, imported interfaces.
  Removing these doesn't block the task but forces extra exploration.

This mapping is **more principled** than AI judgment because E-file ground
truth is deterministic (the PR diff is fact, not inference).

## 7. Non-OK Queries

The current pipeline generates UNSAT/BROAD/AMBIG queries per repo.
For PR-derived GT, these are generated synthetically:

- **UNSAT**: Queries referencing non-existent symbols or deleted APIs
- **BROAD**: Issue titles that are too vague (e.g., "Fix bug")
- **AMBIG**: Queries matching multiple unrelated subsystems

These can be generated post-hoc from the PR dataset by filtering for
ambiguous/vague issue titles and inverting query expectations.

## 8. Validation

`validate_ground_truth.py` is updated to accept PR-derived GT:
- Minimum query count relaxed from 6→4 for narrow tasks (PR issues
  may have less text to derive 6+ distinct query types)
- All other schema constraints preserved
- New `source: "pr-mining"` field to distinguish from AI-generated GT

## 9. Migration Path

1. Old `generate` command preserved as `generate-legacy` (no deletion)
2. New `mine` command becomes the default GT generation path
3. Existing GT data in `data/{repo_id}/ground_truth/` is compatible —
   both sources produce identical schema
4. Can mix AI-generated and PR-mined GT in the same training set
