# SPLADE Sparse Retrieval Bakeoff

**Date:** 2026-04-20
**Repo:** python-rich (232 queries, 14 803 defs, 465 files)
**Hardware:** CPU-only (no GPU)

## Goal

Compare SPLADE sparse retrieval models for code definition retrieval on
anglicised scaffolds. Pick the best model for production use in CodeRecon,
balancing recall quality, throughput, memory, and licence.

## Models Evaluated

| Short Name | HuggingFace ID | Params | Licence |
|---|---|---|---|
| splade-v3-distilbert | `naver/splade-v3-distilbert` | 67 M | CC-BY-NC-SA-4.0 |
| splade-mini | `rasyosef/splade-mini` | 11 M | Apache-2.0 |
| opensearch-v2 | `opensearch-project/opensearch-neural-sparse-encoding-v2-distill` | 67 M | Apache-2.0 |

## Method

1. Build anglicised scaffolds for every definition in the index
   (`path · kind · name · signature · callees · type_refs · docstring`,
   camelCase/snake_case split into natural words).
2. Encode all scaffolds + queries with each SPLADE model (CPU, batch size 32).
3. Score via sparse dot-product, rank, compute file Recall@20 and def Recall@50.
4. Match on `path:kind:name` (no `start_line`) because the index is at HEAD
   while ground-truth is at the PR commit, so line numbers drift.

## Results

| Model | file_R@20 | def_R@50 | Encode (docs/s) | Ret lat p95 | Peak RSS | Active dims |
|---|---|---|---|---|---|---|
| splade-v3-distilbert | 0.649 | 0.188 | 51 | 11.6 ms | 3 728 MB | 109 (p95 190) |
| **splade-mini** | 0.552 | **0.193** | **150** | 14.8 ms | **815 MB** | 127 (p95 215) |
| opensearch-v2 | **0.632** | **0.204** | 48 | 22.7 ms | — | 130 (p95 170) |

## Decision: splade-mini

- **def_R@50 is within 1 pp** of the best model (0.193 vs 0.204) despite being
  6× smaller.
- **3× faster encoding** (150 vs ~50 docs/s) and **4.5× less memory** (815 MB
  vs 3.7 GB).
- **Apache-2.0** — shippable without licence concerns. distilbert is
  CC-BY-NC-SA (non-commercial), ruling it out.
- The 10 pp file_R@20 gap vs distilbert can be closed with scaffold tuning or
  by retrieving more candidates and re-ranking.
- At 11 M params it's feasible to fine-tune on code-specific data later.

## Artifacts

| File | Description |
|---|---|
| `metrics.json` | Per-model aggregate metrics |
| `per_query.parquet` | Per-query recall breakdown |
| `term_samples_*.json` | Sample SPLADE term activations per model |
