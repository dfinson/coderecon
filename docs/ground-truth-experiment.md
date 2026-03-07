# Ground Truth Tier Experiment

## Question

Do `minimum_sufficient_defs` and `thrash_preventing_defs` actually
diverge in practice, or is the two-tier design overengineering?

## Method

Invented 2 realistic tasks on the codeplane repo (1 narrow, 1 medium).
Solved N1 for real (captured diff, reverted). Analyzed M1 by tracing
the code without editing. Produced full ground truth for both, then
compared the two tiers.

## Task N1 (narrow): Add parse_warnings counter to LCOV parser

**What:** `LcovParser.parse` silently swallows `ValueError` via 4×
`except ValueError: pass`. Add a `parse_warnings` counter to
`CoverageReport` and increment it instead of silently passing.

**Diff:** Added `parse_warnings: int = 0` field to `CoverageReport`.
Replaced 4× `except ValueError: pass` with `parse_warnings += 1`.
Updated return to pass the counter.

**Files touched:** `parsers/lcov.py`, `models.py`

| Tier | Defs |
|------|------|
| minimum_sufficient (gain=2) | `lcov.py:parse` (line 60, edited), `models.py:CoverageReport` (line 129, edited) |
| thrash_preventing (gain=1) | `models.py:CoverageParseError` (line 14, agent checks for existing error mechanism), `parsers/base.py:CoverageParser` (line 9, agent checks return type protocol) |

**Tier difference:** Marginal. The 2 thrash_preventing defs each
prevent one confirmation search — an agent would check "should I raise
CoverageParseError instead?" and "does the parse() protocol need
changing?" A human wouldn't bother checking.

## Task M1 (medium): Add uncovered branch details to coverage reports

**What:** `FileCoverage` has `uncovered_lines` but no equivalent for
branches. `_file_coverage_detail` and `build_tiered_coverage` show line
coverage detail but not branch detail. Add `uncovered_branches`
property, include branch detail in both report functions.

**Files touched:** `models.py`, `report.py`

| Tier | Defs |
|------|------|
| minimum_sufficient (gain=2) | `models.py:FileCoverage` (41, edited), `models.py:uncovered_lines` (71, read — pattern), `models.py:BranchCoverage` (18, read — fields), `report.py:_file_coverage_detail` (175, edited), `report.py:build_tiered_coverage` (209, edited) |
| thrash_preventing (gain=1) | `report.py:build_compact_summary` (120, does it need updating?), `report.py:build_coverage_detail` (265, consistency?), `report.py:_compress_ranges` (51, reusable?), `models.py:branches_found` (76, naming convention), `models.py:branch_rate` (86, computation consistency) |

**Tier difference:** Significant. 5 thrash_preventing defs — all are
things an agent would proactively search for: "does the other detail
view need branch info too?", "can I reuse _compress_ranges?", "what's
the naming convention for branch properties?" A human familiar with the
codebase already knows these answers.

## Conclusion (tier experiment)

| Complexity | min_suff | thrash_prev | Ratio | Verdict |
|-----------|----------|-------------|-------|---------|
| Narrow | 2 | 2 | 1:1 | Real but marginal |
| Medium | 5 | 5 | 1:1 | Real and significant |

The tiers genuinely diverge. Graded relevance (2/1/0) ensures minimum
defs survive budget pressure. **Keep both tiers.**

---

## Signal Quality Experiment

### Question

Do the retrieval signals actually separate relevant from irrelevant
candidates? Can a ranker learn to surface ground truth defs?

### Bugs found and fixed

1. **`"coverage"` in `PRUNABLE_DIRS`** — directory walker pruned any
   directory named `coverage` at any depth. 14 source files missing.
   Fixed: removed from prunable, `.cplignore` pattern root-only.

2. **Def embeddings not persisted by `cpl init -r`** — embedding
   computation takes ~90s and runs at the end of indexing. If init
   was killed (SIGPIPE, Ctrl-C, daemon restart), embeddings were
   lost and never rebuilt. Fixed: added `_validate_embeddings()` to
   daemon startup that compares embedded count vs code def count and
   auto-rebuilds if gap >5%.

### Method (run 2 — with embeddings)

6 tasks across code, non-code, and code-island subsystems. 3 query
types each = 21 signal collection runs. Embeddings fully populated
(8,307 def embeddings). Measured: recall, embedding separation,
retriever_hits separation, F1 with simple ranker (sort by hits+emb).

### Tasks

| Task | Complexity | Subsystem | Type | GT defs |
|------|-----------|-----------|------|---------|
| N1 | narrow | testing/coverage | Code | 5 |
| N2 | narrow | mcp/delivery | Code | 3 |
| N4 | narrow | Makefile | Non-code | 2 |
| N5 | narrow | ranking/gate | Code island | 3 |
| M2 | medium | refactor/ops | Code | 7 |
| W1 | wide | testing/coverage/* | Cross-cutting | 10 |

### Results

| Task | Query | Cands | Found | R@10 | R@20 | F1@5 | EmbSep | HitSep |
|------|-------|-------|-------|------|------|------|--------|--------|
| N1 lcov | Q_FULL | 8650 | 5/5 | 2/5 | 3/5 | 0.600 | 1.25× | 3.3× |
| N1 lcov | Q_SEMANTIC | 8584 | 5/5 | 0/5 | 3/5 | 0.000 | 1.21× | 1.9× |
| N1 lcov | Q_IDENT | 8558 | 5/5 | 2/5 | 4/5 | 0.800 | 1.29× | 3.0× |
| N2 delivery | Q_FULL | 8525 | 3/3 | 2/3 | 2/3 | 0.500 | 1.14× | 3.9× |
| N2 delivery | Q_SEMANTIC | 8637 | 3/3 | 3/3 | 3/3 | 0.500 | 1.25× | 1.9× |
| N2 delivery | Q_IDENT | 8615 | 3/3 | 3/3 | 3/3 | 0.750 | 1.27× | 2.9× |
| N4 Makefile | Q_FULL | 8506 | 2/2 | 0/2 | 0/2 | 0.571 | — | 2.9× |
| N4 Makefile | Q_SEMANTIC | 8594 | 1/2 | 0/2 | 0/2 | 0.000 | — | 1.0× |
| N4 Makefile | Q_IDENT | 8449 | 2/2 | 0/2 | 0/2 | 0.000 | — | 1.0× |
| N5 gate | Q_FULL | 8479 | 3/3 | 0/3 | 0/3 | 0.750 | — | 2.9× |
| N5 gate | Q_SEMANTIC | 8704 | 3/3 | 0/3 | 0/3 | 0.000 | — | 0.9× |
| N5 gate | Q_IDENT | 8550 | 3/3 | 0/3 | 0/3 | 0.500 | — | 2.6× |
| M2 refactor | Q_FULL | 8531 | 7/7 | 1/7 | 3/7 | 0.167 | 1.21× | 3.6× |
| M2 refactor | Q_SEMANTIC | 8519 | 7/7 | 2/7 | 2/7 | 0.000 | 1.15× | 1.0× |
| M2 refactor | Q_IDENT | 8548 | 7/7 | 2/7 | 2/7 | 0.667 | 1.21× | 3.2× |
| W1 parsers | Q_FULL | 8624 | 10/10 | 3/10 | 6/10 | 0.133 | 1.21× | 2.2× |
| W1 parsers | Q_SEMANTIC | 8672 | 10/10 | 0/10 | 1/10 | 0.000 | 1.13× | 1.9× |
| W1 parsers | Q_IDENT | 8562 | 10/10 | 7/10 | 8/10 | 0.400 | 1.26× | 2.7× |

"—" = non-code defs have no per-def embeddings (file-level only).

### Aggregate

| Query type | Avg F1@5 | Avg EmbSep | Avg HitSep | Recall@ALL |
|-----------|----------|-----------|-----------|-----------|
| Q_FULL (seeds+pins) | 0.454 | 0.80× | 3.1× | 100% |
| Q_SEMANTIC (no hints) | 0.083 | 0.79× | 1.4× | 97% |
| Q_IDENT (names only) | 0.519 | 0.84× | 2.6× | 100% |

### Key findings (vs run 1 without embeddings)

**Embeddings help but don't dominate.** Embedding separation is
~1.2× for code defs (GT defs score ~0.70 avg, non-GT ~0.57).
This is useful signal for the ranker but weaker than retriever_hits
(~3× separation). The ranker needs both features.

**Q_SEMANTIC improved dramatically.** Run 1 (no embeddings): 0.062
avg F1, 61% recall. Run 2 (with embeddings): 0.083 avg F1, 97%
recall. Recall jumped from 61% to 97% — embeddings find defs that
term match can't. F1 is still low because embedding separation
(1.2×) isn't strong enough alone for top-5 ranking.

**Non-code defs (Makefile) are hard.** No per-def embeddings (file
embedding only), term match separation is weak. F1@5 is good for
Q_FULL (0.571 via pins) but 0.0 for semantic/ident. The ranker will
need the file-embedding signal for non-code defs.

**Code islands (gate.py) work via hits.** Despite no embedding
signal, Q_FULL gets F1=0.750 via retriever_hits (2.9× separation).
Seeds+pins drive the signal.

**Wide tasks (W1) are hardest.** 10 GT defs across 8 files — Q_FULL
only gets F1=0.133 at top 5. But R@20=6/10, meaning a good ranker
with cutoff at ~20 would capture most. Q_IDENT gets F1=0.400 with
7/10 in top 10.

### Verdict

| Scenario | Expected F1 | Evidence |
|----------|------------|---------|
| Best case (perfect ranker) | 1.000 | 100% recall — all GT defs in pool |
| Good (ident queries, simple ranker) | 0.519 | Avg across 6 tasks |
| Typical (full queries, seeds+pins) | 0.454 | Avg across 6 tasks |
| Semantic only | 0.083 | Weak signal, but 97% recall |

**Retriever_hits is the primary discriminator** (~3× separation).
Embeddings add ~1.2× on top. A LambdaMART model combining both with
structural features (path, kind, has_docstring, etc.) should push
F1 well above 0.5 for typical queries.

**Recall is 100% for non-semantic queries.** The harvesters always
find the GT defs — the problem is purely ranking. This is exactly
what LambdaMART is designed to solve.

**The investment is justified.**
