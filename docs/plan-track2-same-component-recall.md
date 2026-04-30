---
title: "Track 2: Same-Component Recall Improvement Plan"
description: "Internal plan to improve retrieval recall within the reachable graph component from 23% to 70%+"
author: coderecon-team
ms.date: 2026-04-30
ms.topic: concept
---

## Problem Statement

The candidate pool (pre-ranker) recovers only **23.3% of same-component GT defs**
using the current harvester pipeline. The remaining 77% of defs are structurally
connected to the seeds (same graph component, reachable within 5 hops) but never
enter the pool.

### Key Data Points

| Metric | Value | Source |
|--------|-------|--------|
| Total (query, GT_def) pairs evaluated | 67,637 | recall_gap_fast.py across 11 repos, 320 PRs |
| Same-component pairs | 12,925 (19.1%) | Connected-component BFS |
| Different-component pairs (unreachable) | 54,712 (80.9%) | Multi-concern PR artifact |
| 1-hop recall over same-component GT | 23.3% | graph + same-file only |
| GT def distance distribution (same-component) | 1-hop: 10%, 2-hop: 27%, 3-hop: 23%, 4-5: 22%, 6+: 18% | BFS from seeds |

### Current Pipeline Architecture

The retrieval pipeline in `raw_signals.py` runs harvesters in sequence:

1. **B — Term match** (`_harvest_term_match`): SQL LIKE + Tantivy BM25. Name/path matching.
2. **S — SPLADE** (`_harvest_splade`): Query-text encoding → dot product against stored vecs.
   Retrieves by semantic similarity to the *query text*, not to the *seed defs*.
3. **D — Explicit** (`_harvest_explicit`): Agent-provided seeds + parsed paths/symbols.
4. Merge B-S-D.
5. **E — Graph walk** (`_harvest_graph`): **1-hop only** from top merged candidates.
   Expands callees, callers, siblings, co-implementors, doc_xrefs.
6. **F — Imports** (`_harvest_imports`): 1-hop forward + reverse import edges from
   seed files. Also barrel files and test-pair inference.
7. **Coverage expansion** (`_expand_via_coverage`): Test candidates → source defs
   they cover. Currently empty (`test_coverage_facts` has 0 rows).

### Why 77% Is Missed

The pipeline has **one shot** at each signal. The graph walk is 1-hop, SPLADE only
uses query text (not seed vocabulary), and imports are 1-hop. Defs at depth 2-5 fall
through unless they happen to match query terms.

---

## Proposed Changes

### Change 1: SPLADE Seed-Centroid Retrieval (New Harvester)

**Status**: Proven in experiment. +11pp recall (measured against full GT denominator).
Against same-component GT only, this is the single largest contributor.

**Mechanism**: Average the SPLADE vectors of all seed/explicit defs into a centroid.
Score all stored def vectors by cosine similarity to the centroid. Add all defs above
the sparsity floor to the pool.

**Why it works**: Defs at depth 2-5 share vocabulary patterns with the seed cluster
(same domain, same naming conventions, same imports) even when no direct call/ref
edge connects them. The centroid captures the "topic fingerprint" of the seed
neighborhood and finds vocabulary-similar defs across the graph.

**Implementation**:

- New function `_harvest_splade_centroid` in `harvesters.py`
- Input: `merged` pool (post B-S-D merge), `app_ctx`
- Steps:
  1. Collect SPLADE vectors for all explicit/pinned candidates (seed set)
  2. Average into dense centroid (vocab_size=30522 floats)
  3. L2-normalize centroid
  4. Dot product against all stored vecs (reuse `load_all_vectors_fast`)
  5. Apply same score_floor as query SPLADE (sparsity gate)
  6. Emit candidates with `from_splade_centroid=True`, score attached
- Insert point: After B-S-D merge, before graph walk (so graph can expand from
  centroid hits too)
- No budget cap: sparsity + score floor are the natural filter. Ranker discriminates.

**Expected lift**: +30-40pp on same-component recall (pulls depth 2-5 defs that
share seed vocabulary).

**Risk**: Pool size increase. Measured average pool size with centroid: ~500 defs
(4.9% of repo). Ranker already handles this volume.

---

### Change 2: 2-Hop Graph Walk

**Status**: Trivial extension. BFS data shows 27% of same-component GT is at
exactly depth 2.

**Mechanism**: After the current 1-hop graph walk emits candidates, run a second
pass: take the newly-found 1-hop candidates as intermediate seeds, walk their
callees/callers/siblings. Add depth-2 discoveries to the pool.

**Implementation**:

- Modify `_harvest_graph` in `graph_harvester.py`
- After the current single-pass walk:
  1. Collect UIDs emitted in pass 1 (1-hop results)
  2. For each, expand callees + callers (skip siblings at depth 2 to control noise)
  3. Tag with `graph_depth=2`, lower evidence score (0.7 vs 1.0 for depth 1)
- Alternative: configurable `max_depth` parameter on the harvester (default 2)

**Expected lift**: +15-20pp on same-component recall.

**Risk**: Quadratic blowup if seeds have high fan-out. Mitigate by:

- Only walk depth-2 from the subset of 1-hop results that have `evidence_axes >= 2`
  (already supported via `_select_graph_seeds` logic)
- Skip expanding type-hierarchy "hub" nodes (classes with 50+ callers)

---

### Change 3: 2-Hop Import Transitivity

**Status**: Data shows 4.1% of graph-unreachable GT is reachable via 2-hop file
imports. Within same-component, the fraction is higher.

**Mechanism**: The current import harvester traces 1-hop forward + reverse.
Extend to 2-hop: for each file found at depth 1, trace its imports/importers
one more level.

**Implementation**:

- Modify `_harvest_imports` in `harvesters.py`
- After collecting 1-hop import results:
  1. Collect file IDs of all 1-hop discovered files
  2. Trace their forward imports (files they import)
  3. Tag depth-2 imports with `import_direction="forward_2hop"`, lower score
- Do NOT do reverse-2-hop (too noisy — every file importing a utility module)

**Expected lift**: +3-5pp on same-component recall.

**Risk**: Low. Forward-only 2-hop is bounded by what the depth-1 files actually
import (typically 5-15 files each).

---

### Change 4: Reverse Coverage Expansion (seed → tests)

**Status**: Infrastructure exists (`_expand_via_coverage` in `merge.py`,
`blast_radius.py`). Blocked on populating `test_coverage_facts`.

**Mechanism**: Given source defs in the pool, find tests that cover them via
`test_coverage_facts`. Add those test defs to the pool.

**Current code gap**: `_expand_via_coverage` only works in ONE direction:
test candidates in pool → source defs they cover. It does NOT do the reverse:
source seed candidates → tests that cover those seeds.

**Implementation**:

- Add reverse path in `_expand_via_coverage` (`merge.py` line 202):

  ```python
  # Current: test candidates → source defs
  # Add: source seed candidates → test defs that cover them
  source_uids = [c.def_uid for c in candidates.values()
                 if not c.is_test and (c.from_explicit or c.evidence_axes >= 2)]
  if source_uids:
      with coordinator.db.session() as session:
          fq = FactQueries(session)
          test_uids = fq.batch_get_covering_test_uids(source_uids)
          # ... emit as HarvestCandidate with from_coverage=True
  ```

- Requires: `batch_get_covering_test_uids` query on `test_coverage_facts`:

  ```sql
  SELECT DISTINCT test_id FROM test_coverage_facts
  WHERE target_def_uid IN (:uids) AND stale = 0
  ```

- Prerequisite: `test_coverage_facts` must be populated by the daemon's
  coverage ingestion pipeline.

**Measured lift (celery PR10038)**: Reverse coverage recovers 22 additional GT defs
(+32pp recall) but at high pool cost — 54 test files, 2570 extra defs. Requires
tight scoping (only expand from high-confidence seeds, not the whole pool) to
avoid precision collapse. Across the eval set, expect +5-10pp same-component recall
with appropriate pool-size controls.

**Dependency**: Coverage data population — separate workstream.

---

## Execution Order

| Priority | Change | Effort | Dependency | Lift |
|----------|--------|--------|------------|------|
| 1 | SPLADE centroid | Medium (new harvester) | None | +30-40pp |
| 2 | 2-hop graph | Small (extend existing) | None | +15-20pp |
| 3 | 2-hop imports (forward only) | Small | None | +3-5pp |
| 4 | Reverse coverage | Small (code exists) | `test_coverage_facts` populated | +5-10pp |

Changes 1-3 are independent and can ship together. Change 4 is gated on the
coverage ingestion workstream.

**Overlap note**: SPLADE centroid and 2-hop graph will partially overlap (both
reach depth-2-3 defs). Realistic combined lift accounting for overlap: **+45-55pp**
on same-component recall, bringing it from 23% to **~70-78%**.

**Validated ceiling (celery PR10038 — hardest category, all disconnected classes)**:
Selective signals (seeds, pins, graph, term match) yield **28% recall at 5.7%
precision** (331 defs, 2.9% of repo). Adding reverse coverage reaches 60% recall
but at 1.4% precision (2901 defs, 26% of repo — coverage fans out through 54 test
files). Import expansion is worse: 75% recall at <1% precision (55% of repo).

The 70-78% target requires precision-aware expansion — scored candidates via
SPLADE centroid and selective graph walks — not brute-force file-level sweeps.

### Known Residual: Repetitive-Pattern Defs

A small fraction of GT defs (~4% in the celery walkthrough) share zero structural
connection to any seed: no imports, no refs, no shared interfaces, no co-coverage.
These are files that need the same mechanical edit (e.g., add `__class_getitem__`)
but exist in unrelated subsystems.

This category is **not addressable by pool expansion**. It requires multi-turn
agentic reasoning: recognize the edit pattern from completed changes, derive a
search criterion, and re-query. This is out of scope for Track 2 (retrieval)
and belongs to the agent-loop / multi-turn planning layer.

---

## Evaluation Methodology Fix (Context)

The current eval scores each query against ALL defs touched by the PR. Since 43%
of PRs are multi-concern (touching disconnected modules), 81% of (query, GT_def)
pairs involve defs in a completely separate graph component from the seeds.

**Impact on metrics**: Any same-component recall improvement will appear diluted
by ~5× when measured against the full GT denominator. A +40pp same-component gain
shows as only ~+8pp on the legacy metric.

**Recommendation**: Report two metrics:

1. **Same-component recall** (primary): only count GT defs in the same connected
   component as the query's seeds. This is the metric Track 2 optimizes.
2. **Full recall** (secondary): legacy metric for backward compatibility.

The eval fix (per-query GT labeling) is a separate Track 1 workstream.

---

## Measurement Plan

### Offline Experiment (pre-ship validation)

Extend `recall_gap_fast.py` to:

1. Simulate SPLADE centroid: average seed vectors → score all vecs → merge into pool
2. Simulate 2-hop graph: BFS depth 2 from seeds
3. Simulate 2-hop imports: forward imports of 1-hop import results
4. Report same-component recall for each combination

Validation criteria:

- Same-component recall ≥ 85% with changes 1+2+3+4 combined
- Pool size median ≤ 800 defs (manageable for ranker)
- No regression on precision@20 files (ranker output quality)

### Online Validation (post-ship)

- A/B on ranker-gate eval set
- Track: files_suggested that are in PR diff (hit rate)
- Track: pool size distribution (p50, p95)
- Track: latency impact (centroid scoring adds one matrix multiply)

---

## Technical Details

### SPLADE Vector Storage

- Model: `splade-mini-onnx-v1`
- Vocab size: 30,522
- Average non-zeros per vector: ~37
- Storage: `splade_vecs` table, `vector_blob` column
- Binary format: packed `(int32 token_id, float32 weight)` pairs (8 bytes/entry)
- Total vecs per repo: 2,000-92,000 (varies by repo size)

### Centroid Computation Cost

- Load all vecs: already cached in `load_all_vectors_fast`
- Average N seed vecs (N typically 10-50): negligible
- Score all M stored vecs against centroid: one sparse matmul (M × sparse dot)
- Expected latency: <50ms for 90k-def repos (same as query SPLADE)

### Graph Walk Cost

- Current 1-hop: O(seeds × avg_degree). Avg degree ~5. Seeds ~20. = ~100 lookups
- 2-hop: O(hop1_results × avg_degree). Hop1 ~100 results. = ~500 lookups
- All via batch SQL queries (already implemented in `FactQueries`)
- Expected latency delta: <20ms

### Pool Size Budget

Current pool size distribution (from recall_gap_fast):

- Median: ~200 defs
- P75: ~400 defs
- P95: ~800 defs

With centroid + 2-hop:

- Expected median: ~500 defs
- Expected P95: ~1200 defs
- Ranker can handle: tested up to 2000 candidates without latency regression

---

## Files to Modify

| File | Change |
|------|--------|
| `src/coderecon/mcp/tools/recon/harvesters.py` | Add `_harvest_splade_centroid` function |
| `src/coderecon/mcp/tools/recon/raw_signals.py` | Insert centroid harvester after B-S-D merge |
| `src/coderecon/mcp/tools/recon/graph_harvester.py` | Add depth-2 expansion pass |
| `src/coderecon/mcp/tools/recon/harvesters.py` | Extend `_harvest_imports` with 2-hop forward |
| `src/coderecon/mcp/tools/recon/merge.py` | Add reverse direction to `_expand_via_coverage` |
| `src/coderecon/mcp/tools/recon/models.py` | Add `from_splade_centroid` field to `HarvestCandidate` |
| `src/coderecon/index/search/splade_db.py` | Add `score_against_centroid(centroid_vec)` utility |
| `recon-lab/scripts/recall_gap_fast.py` | Add same-component metric + centroid simulation |

---

## Open Questions

1. **Centroid normalization**: L2-normalize before averaging, or average raw weights
   then normalize? Experiment showed averaging raw → normalize works. Confirm on
   full eval set.
2. **2-hop graph seed selection**: Walk depth-2 from ALL 1-hop results, or only from
   those with multi-signal evidence? Need to measure pool size impact.
3. **Ranker feature**: Should `splade_centroid_score` be a separate ranker feature
   (in addition to `splade_score` from query-text retrieval)? Likely yes — they
   capture different signals (query relevance vs. seed neighborhood).
4. **Coverage population timeline**: When will `test_coverage_facts` be populated
   for eval repos? This gates Change 4.
