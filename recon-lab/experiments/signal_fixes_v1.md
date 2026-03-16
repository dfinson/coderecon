# Signal Fixes v1 — Harvester Pipeline Improvements

## Experiment Goal
Fix 4 root causes identified in harvester signal analysis that cripple
the multi-retriever pipeline, then measure improvement.

## Subset Repos (5 repos, 5 languages)

| Repo | Lang | Defs | Indexed GT | Recall Ceiling |
|------|------|------|------------|----------------|
| sinatra | ruby | 1,438 | 32 | 65.3% |
| gin | go | 2,061 | 15 | 35.7% |
| console | php | 4,991 | 34 | 50.0% |
| mockito | java | 9,819 | 15 | 44.1% |
| celery | python | 11,382 | 16 | 28.6% |
| **Total** | | **29,691** | **112** | |

## Fixes

### Fix 1: Wire Tantivy BM25 for term match
- **Root cause**: `_harvest_term_match` uses SQL LIKE on def names only.
  Tantivy full-text index exists but is never used for candidate harvesting.
  Combined with aggressive stop words, term_match returns 0% for relevant defs.
- **Change**: Add Tantivy BM25 file scoring → expand top files to defs.
  Supplements existing SQL LIKE with full-text search on file content+symbols.
- **Files**: `harvesters.py` (`_harvest_term_match`)

### Fix 2: Wire `_harvest_imports`
- **Root cause**: Import harvester is fully implemented but never called —
  not imported in `raw_signals.py`.
- **Change**: Import and call `_harvest_imports` after graph harvest in pipeline.
- **Files**: `raw_signals.py`
- **Bug found during testing**: Two additional bugs in `_add_file_defs_as_candidates`
  and `_harvest_imports` itself:
  1. `_add_file_defs_as_candidates` had `if not from_graph` guard that silently
     skipped setting `import_direction` on defs already marked by graph harvester.
  2. Forward/reverse import traversal excluded seed files (`if imp_file.id in
     seed_file_ids`). Since relevant defs tend to have evidence_axes >= 2, their
     files are almost always seeds, so they never got import_direction.
- **Additional files**: `merge.py` (_add_file_defs_as_candidates), `harvesters.py`
  (_harvest_imports forward/reverse traversal)

### Fix 3: Use `_build_query_views` for embedding
- **Root cause**: Embedding harvester uses raw NL query text, but the
  embedding model (bge-small-en-v1.5) was trained on general English.
  `_build_query_views()` exists in parsing.py to generate multi-view
  queries (NL + code-style + keyword-focused) but is never called.
- **Change**: Call `_build_query_views(parsed)` in `_harvest_def_embedding`,
  query each view, merge by max similarity.
- **Files**: `harvesters.py` (`_harvest_def_embedding`)

### Fix 4: Count embedding as evidence axis
- **Root cause**: `evidence_axes` only counts term_match + explicit + graph.
  Embedding is not counted, so candidates found by embedding + term_match
  still show evidence_axes=1 (only term_match counted). This means the
  graph seed selector (requires axes >= 2) ignores most candidates.
- **Change**: Add `from_embedding` flag to `HarvestCandidate`, set it in
  embedding harvester, merge it, count it in `evidence_axes`.
- **Files**: `models.py`, `harvesters.py`, `merge.py`

## Baseline Metrics (pre-fix)
88 queries, 216 relevant candidates, 488,836 irrelevant

| Metric | Value |
|--------|-------|
| term_match coverage (relevant) | **0.0%** |
| term_match ratio (rel/irrel mean) | 1.00x |
| emb_score ratio (rel/irrel mean) | 1.14x |
| graph coverage (relevant) | 44.0% |
| graph_seed_rank ratio | 13.31x |
| retriever_hits=1 (single-signal) | 19.0% |
| retriever_hits=4 | 25.9% |

## Post-Fix Metrics
88 queries, 216 relevant candidates, 488,836 irrelevant

| Metric | Pre-fix | Post-fix v2 | Post-fix v3 (import fix) | Change v1→v3 |
|--------|---------|-------------|--------------------------|--------------|
| **term_match coverage (rel)** | **0.0%** | **92.6%** | **92.6%** | **+92.6pp** |
| term_match ratio | 1.00x | **8.59x** | 8.59x | +7.59x |
| emb_score ratio | 1.14x | 1.14x | 1.14x | ±0 |
| **graph coverage (rel)** | **44.0%** | **88.0%** | **88.0%** | **+44pp** |
| graph_seed_rank ratio | 13.31x | 0.20x | 0.20x | Inverted (more seeds) |
| **import coverage (rel)** | **N/A** | **0.0%** | **67.1%** | **+67.1pp** |
| import forward | N/A | 0.0% | 51.9% | |
| import reverse | N/A | 0.0% | 15.3% | |
| **retriever_hits=1** | **19.0%** | **4.6%** | **4.2%** | **-14.8pp** |
| retriever_hits=4 | 25.9% | 33.8% | 47.7% | +21.8pp |
| retriever_hits=5 | 0.0% | 0.0% | **26.4%** | **+26.4pp** |

### Key observations
1. **Fix 1 (Tantivy BM25)**: term_match went from dead to 92.6% coverage
   with 8.59x discrimination. Biggest single improvement.
2. **Fix 4 (embedding as evidence axis)**: Graph coverage doubled (44→88%).
   Embedding + term_match → evidence_axes=2 → graph seeds → 1-hop expansion.
3. **Fix 3 (multi-view embedding)**: Minimal impact on emb_score discrimination.
   The bge-small-en-v1.5 model is too general to benefit from reformulation.
4. **Fix 2 (imports)**: Initially 0% — two bugs prevented import_direction
   from being set on relevant defs (from_graph guard + seed file exclusion).
   After fixing, 67.1% coverage with 51.9% forward + 15.3% reverse.
5. **Merge bug fixed**: `_merge_candidates` wasn't propagating term_match_count,
   term_total_matches, graph_edge_type, graph_seed_rank, or import_direction
   across harvesters. Fixed.

## Vibe Check Comparison (existing models, NOT retrained)

| Metric | Baseline | Ranking | Delta |
|--------|----------|---------|-------|
| NDCG@5 | 0.1733 | 0.1234 | -0.0499 |
| Hit@5 | 0.3523 | 0.2727 | -0.0795 |
| Hit@10 | 0.3750 | 0.3409 | -0.0341 |
| Rec@10 | 0.2095 | 0.2116 | +0.0021 |
| Cutoff F1 | 0.0638 | 0.0657 | +0.0018 |
| Pred N | 20.0 | 14.7 | -5.3 |

**NOTE**: Ranking model trained on OLD signal distribution (term_match=dead,
graph=sparse). Now that signals changed dramatically (term_match alive,
graph dense), the model's learned weights are miscalibrated. The model
needs retraining on the new pipeline output to benefit from these fixes.

## Next Steps
1. **Retrain models** on new pipeline signal data (re-collect signals
   from training repos using the fixed pipeline)
2. **Re-evaluate** with retrained models to measure true impact
3. Consider dropping multi-view embedding (Fix 3) — it adds latency
   without improving discrimination for bge-small-en-v1.5
