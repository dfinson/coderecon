---
title: Cross-Encoder Reranking Bakeoff
description: Evaluation of cross-encoder rerankers for CodeRecon's hierarchical retrieval pipeline
---

Evaluate cross-encoder rerankers in CodeRecon's hierarchical retrieval
pipeline: compare models, insertion points, representations, and fusion
strategies against the existing LightGBM + RRF baseline.

---

## 1. Architectural Evaluation

### Insertion points

| Point | What it reranks | Candidate count | Verdict |
|-------|----------------|-----------------|---------|
| **File stage** | Top-K files from first-stage retrieval | ~20–50 files | Low value. File ranking is coarse; the file-level aggregated signal (sum of def vectors / BM25 hits) is already reasonable. Cross-encoder on file-level scaffolds adds marginal discrimination. Cost is low but payoff is also low. |
| **Def stage** | Defs within selected files | ~50–200 defs | **Highest value.** This is where ranking quality lives. First-stage retrieval gets defs into the candidate pool; cross-encoder separates relevant from irrelevant within that pool. Natural language queries need to match against structured code — exactly the gap lexical methods struggle with. |
| **Final global rerank** | Union of defs from retrieval + graph expansion | ~30–100 items | High value if graph expansion injects noise. A final cross-encoder pass filters graph-expanded neighbors that are structurally related but semantically irrelevant to the query. |
| **Cascaded** | Cheap reranker first, strong reranker on top-K | varies | Only justified when candidate counts exceed ~100. At ~50 candidates, a single MiniLM-L-6 pass is <500ms on CPU. Cascading adds complexity for negligible latency savings. |

### Best placement

**Def-stage reranking is where most value lives.** Rationale:

1. File retrieval is a coarse filter — getting the right files into the
   pool is mostly solved by BM25/SPLADE + structural signals. Wrong-file
   errors are usually first-stage recall failures, not ranking failures.
2. Within-file def discrimination is the hard problem. A query like
   "rate limiting middleware" needs to prefer `check_rate_limit()` over
   `format_response()` in the same file — lexical overlap is weak here.
3. Graph expansion adds structurally related defs (callees, parents) that
   may or may not match the query intent. A post-expansion rerank filters
   these.

**Recommendation: rerank defs, not files.** Optionally add a final pass
after graph expansion if expansion injects >30% noise.

### Graph expansion timing

Graph expansion should happen **before** the final cross-encoder pass:

- Graph expansion is cheap (SQL lookups on the structural graph).
- It adds candidates the first-stage retriever missed (callees, parents).
- Cross-encoder after expansion scores everything uniformly, including
  graph-injected candidates.
- Reranking before expansion risks pruning a borderline def whose
  graph-expanded neighbors are highly relevant.

### Code vs docs: separate or joint?

**Separate rerank lanes, then merge.** Rationale:

- Code scaffolds and doc sections have different text distributions.
- A cross-encoder trained on MS MARCO will handle natural-language doc
  sections better than code scaffolds.
- Code scaffolds need anglicized representation; docs are already text.
- Separate lanes allow different candidate counts (more defs, fewer doc
  sections).
- Final merge via RRF or score normalization.

### Comparison matrix

| Configuration | Expected quality | Latency | Complexity | Verdict |
|--------------|-----------------|---------|------------|---------|
| No cross-encoder (LGBM only) | Baseline | Fastest | Lowest | Current system. Decent at structural signals, weak at semantic matching. |
| LGBM only (current) | Baseline | ~5ms | Low | Missing semantic query-doc matching signal. |
| Cross-encoder only | +10–20% def recall | ~200–800ms | Medium | Strong semantic signal but loses structural features (hub score, nesting, graph edges). |
| **Cross-encoder score as LGBM feature** | **+15–25% def recall** | ~200–800ms | Medium | **Best of both worlds.** LGBM learns optimal weighting of semantic score against structural features. |
| Cross-encoder + LGBM cascaded | Similar to above | ~250–850ms | Higher | Marginal gain over feeding score as feature. Not worth the complexity unless candidate counts are very high. |

---

## 2. Representation Design

### Code candidates

| Option | Description | Quality | Robustness | Speed | NL-matching |
|--------|------------|---------|------------|-------|-------------|
| **A: Scaffold only** | split name, path, signature, comments, callees, return expr | Good | High (deterministic, compact) | Fastest (shortest text) | Good — anglicized names read as natural language |
| **B: Scaffold + trimmed body** | Scaffold + first 512 tokens of raw code body | Better | Medium (body varies in quality; comments help, boilerplate hurts) | Slower (longer input) | Better — raw code has comments, variable names |
| **C: Raw code only** | Full function body, no scaffold | Worse | Low (formatting noise, no structural signal) | Slowest (longest) | Weakest — code syntax ≠ natural language |
| **D: Scaffold + graph context** | Scaffold + "called by X", "calls Y", "imported from Z" | Best for structural queries | Medium (graph context can be noisy) | Medium | Best for navigational queries, weaker for semantic |

**Recommendation: Start with A (scaffold only).** Rationale:

1. The SPLADE bakeoff already validated that scaffolds carry sufficient
   signal for retrieval. Cross-encoders are stronger at matching than
   bi-encoders, so scaffolds should be enough.
2. Scaffold text is short (~50–150 tokens). Cross-encoder latency scales
   linearly with input length. Short inputs = more candidates reranked
   within budget.
3. Scaffold is deterministic and reproducible — no tokenization edge cases
   from raw code.
4. Ablation B (scaffold + body) is the **highest-priority ablation** to
   test. If body adds >2% recall, include it.

### Scaffold format for cross-encoder input

```
module auth middleware rate limiter
function check rate limit(request, limit)
in rate limiter middleware
calls get client ip, increment counter, is whitelisted
uses Request, RateLimitConfig
describes Check whether the incoming request exceeds the rate limit
```

This is fed as the "document" side of a `(query, document)` pair to the
cross-encoder.

### Doc candidates

| Option | Description |
|--------|------------|
| Heading path + section text | `"Configuration > Rate Limiting > Per-client limits\n\nEach client is rate-limited to..."` |
| + Neighboring headings | Adds sibling headings for context |
| + Parent context | Prepends parent section summary |

**Recommendation: heading path + section text.** Neighboring headings add
noise more often than signal. Parent context is useful only for very short
sections (<50 tokens).

---

## 3. Model Evaluation Plan

### Shortlist

| Model | Params | Max seq | CPU latency (20 items) | Quality (MS MARCO) | License | Notes |
|-------|--------|---------|----------------------|--------------------|---------|----|
| `cross-encoder/ms-marco-TinyBERT-L-2-v2` | 4.4M | 512 | **~120ms** | MRR@10: 0.321 | Apache-2.0 | Fastest. Baseline for latency budget. |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | 22.7M | 512 | **~300ms** | MRR@10: 0.349 | Apache-2.0 | Best CPU value. 6 layers, good quality. |
| `cross-encoder/ms-marco-MiniLM-L-12-v2` | 33.4M | 512 | **~550ms** | MRR@10: 0.364 | Apache-2.0 | Strongest practical. 12 layers. |
| `BAAI/bge-reranker-v2-m3` | 568M | 8192 | **~4–6s** | State-of-art | MIT | Too slow for CPU. Long context is nice but irrelevant at scaffold lengths. |
| `jinaai/jina-reranker-v2-base-multilingual` | 278M | 8192 | **~2–3s** | Near SOTA | Apache-2.0 | Borderline. Could work for <10 candidates. |

### Code-specific rerankers: reality check

**There are essentially no practical code-specific cross-encoder rerankers
available today.** Reasons:

- CodeBERT (Microsoft, 2020) was an encoder, not a cross-encoder. No
  published cross-encoder fine-tune exists.
- UniXcoder, CodeT5, StarCoder are generative or bi-encoder models.
- The CodeSearchNet benchmark produced bi-encoder models, not rerankers.
- Academic papers on code reranking (e.g., ReACC) are not released as
  usable models.

**Implication:** We must use general-purpose MS MARCO cross-encoders and
rely on anglicized scaffolds to bridge the code→text gap. This is actually
fine — the scaffold builder already anglicizes code into natural-language
text that MS MARCO models can score.

Fine-tuning a MiniLM cross-encoder on our own (query, scaffold, relevant)
triples is the correct long-term path, but requires a training set of
~10K+ labeled pairs. The lab already produces these from PR ground truth.

### Rankings

| Criterion | Best model |
|-----------|-----------|
| Best quality (local CPU) | `ms-marco-MiniLM-L-12-v2` |
| Best CPU value (quality/latency) | **`ms-marco-MiniLM-L-6-v2`** |
| Best fast prototype | `ms-marco-TinyBERT-L-2-v2` |
| Best long-term | Fine-tuned `MiniLM-L-6-v2` on lab data |

---

## 4. Query-Time Pipeline Design

### Candidate flows evaluated

#### Flow A — single final rerank
```
retrieve files → retrieve defs → graph expand → cross-encoder rerank all
```
- Pro: simplest, one rerank pass.
- Con: graph expansion on un-reranked defs may expand noise.
- Candidate count at rerank: ~100–200 (defs from top files + graph).
- Latency: ~500ms–1.5s for MiniLM-L-6 on 100–200 items. Borderline.

#### Flow B — rerank files, then defs
```
retrieve files → rerank files → retrieve defs in top files → rerank defs
```
- Pro: cleaner file selection improves def candidate pool.
- Con: file reranking adds latency for marginal value; two model loads.
- Latency: ~600ms (files) + ~400ms (defs). Over budget.
- **Verdict: not recommended.** File reranking ROI is low.

#### Flow C — rerank defs, then graph expand around top defs (RECOMMENDED)
```
retrieve files → retrieve defs → rerank defs → graph expand around top-K reranked defs
```
- Pro: graph expansion is focused — only expand from high-confidence defs.
  Cross-encoder reranks a smaller set (~50 defs). Graph adds ~10–30 more.
- Con: graph-expanded items are unscored by cross-encoder.
- Fix: score graph-expanded items with cross-encoder too (cheap, <30 items).
- **Latency: ~300ms (50 defs) + ~100ms (graph expand + score 20 neighbors) = ~400ms.** Within budget.

#### Flow D — two-pass cascaded
```
retrieve files → retrieve defs → TinyBERT rerank 200 defs → MiniLM-L-6 rerank top-50
```
- Pro: handles large candidate pools.
- Con: unnecessary complexity at our candidate counts (~50–100).
- **Verdict: only useful if candidate counts exceed ~150.**

### Recommended flow (Flow C variant)

```
1. First-stage retrieval → top-20 files                           [existing, ~50ms]
2. First-stage retrieval → top-100 defs within those files        [existing, ~30ms]
3. Cross-encoder rerank top-100 defs → take top-30                [new, ~300ms]
4. Graph expand 1-hop from top-30 → add ~20 neighbor defs         [existing, ~20ms]
5. Cross-encoder score the ~20 new neighbors                      [new, ~100ms]
6. Merge + sort all ~50 scored defs                               [trivial]
7. LGBM with cross-encoder score as feature → final ranking       [existing, ~5ms]
```

**Total added latency: ~400ms on CPU.** Acceptable.

---

## 5. Feature Fusion with LGBM

### Options

| Strategy | How | Pro | Con |
|----------|-----|-----|-----|
| **Replace LGBM with cross-encoder** | Cross-encoder score is the final ranking signal | Simple | Loses structural features (hub score, graph edges, nesting depth) |
| **Cross-encoder score as LGBM feature** | Add `ce_score` as column 58 in CandidateRank | LGBM learns optimal weighting | Requires retraining LGBM |
| **LGBM first, cross-encoder final** | LGBM selects top-50, cross-encoder reranks | Reduces cross-encoder candidates | LGBM may already rank well; double sorting |
| **RRF of LGBM rank + cross-encoder rank** | $\text{score} = \frac{1}{k + r_{lgbm}} + \frac{1}{k + r_{ce}}$ | No retraining needed | Suboptimal weighting; ignores score magnitudes |
| **Weighted sum** | $\text{score} = \alpha \cdot \hat{s}_{ce} + (1-\alpha) \cdot \hat{s}_{lgbm}$ | Simple, tunable | Requires score normalization; single α is rigid |

### Recommendation

**Cross-encoder score as an LGBM feature** is the strongest approach:

1. LGBM already handles heterogeneous feature types well (scores, counts,
   booleans, categoricals).
2. Adding `ce_score` as one more float feature requires zero architectural
   changes — just add the column to CandidateRank and retrain.
3. LGBM can learn interaction effects: e.g., "high ce_score + hub_score=0
   → probably relevant utility function" vs "high ce_score + is_test=1 →
   probably irrelevant test helper."
4. No score normalization needed — LGBM handles arbitrary scales.
5. Backward compatible: if cross-encoder is unavailable at inference time,
   set `ce_score = 0.0` and LGBM gracefully ignores it.

### Formula

Collect step adds `ce_score` to CandidateRank:

```python
@dataclass(frozen=True)
class CandidateRank:
    ...
    ce_score: float = 0.0  # cross-encoder relevance score
```

Training incorporates it as feature 58. No other changes to the training
pipeline.

---

## 6. Ablations Priority Matrix

| Ablation | Priority | Rationale |
|----------|----------|-----------|
| Def rerank only (scaffold, MiniLM-L-6) | **HIGH** | Core experiment. Must test first. |
| Scaffold only vs scaffold+body | **HIGH** | Determines representation strategy. |
| TinyBERT vs MiniLM-L-6 vs MiniLM-L-12 | **HIGH** | Model selection. Run all three. |
| Top-20 vs top-50 vs top-100 candidates | **HIGH** | Determines latency/quality tradeoff. |
| Cross-encoder before vs after graph expansion | **MEDIUM** | Tests Flow C vs Flow A. |
| Cross-encoder + LGBM feature vs cross-encoder only | **MEDIUM** | Tests fusion strategy. |
| Code/docs separate lanes then merge | **MEDIUM** | Tests joint vs separate scoring. |
| File rerank only | **LOW** | Expected low ROI per architectural analysis. |
| Docs rerank only | **LOW** | Docs are a small fraction of candidates. |
| Two-pass cascaded (TinyBERT → MiniLM) | **LOW** | Only useful if candidate counts are high. |
| Cross-encoder replacing LGBM entirely | **PROBABLY WASTE** | Loses structural features for no benefit. |
| bge-reranker-v2-m3 | **PROBABLY WASTE** | Too slow for CPU. Not practical. |

---

## 7. Final Recommendation

### Best reranking architecture

**Cross-encoder def-stage reranking with score fed as LGBM feature.**

### Exact runtime order

```
1. BM25/SPLADE retrieve → top-20 files                                 [existing]
2. BM25/SPLADE retrieve → candidate defs within top-20 files           [existing]
3. Build scaffold text for each candidate def                          [existing]
4. Cross-encoder score (query, scaffold) for each candidate            [NEW]
5. Graph expand 1-hop from top-30 cross-encoder-scored defs            [existing]
6. Cross-encoder score newly added graph neighbors                     [NEW]
7. LGBM rank with ce_score as feature → final ranking                  [existing, retrained]
8. Cutoff model → decide how many results to return                    [existing]
```

### Model selection

| Role | Model | Rationale |
|------|-------|-----------|
| **Start with** | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Best quality/latency tradeoff. ~300ms for 50 candidates on CPU. |
| **Faster fallback** | `cross-encoder/ms-marco-TinyBERT-L-2-v2` | ~120ms for 50 candidates. Use when latency budget is tight. |
| **Strongest practical** | `cross-encoder/ms-marco-MiniLM-L-12-v2` | ~550ms for 50 candidates. Use when quality matters more than latency. |

### Candidate counts per stage

| Stage | Candidates | Model |
|-------|-----------|-------|
| Def rerank | Top 50–100 from first-stage | MiniLM-L-6 |
| Graph-expanded neighbors | ~20–30 new items | MiniLM-L-6 |
| Final LGBM | All scored items (~70–130) | LightGBM |

### Representation text

**Scaffold only** (same as SPLADE bakeoff scaffold builder). Ablate
scaffold+trimmed-body as highest-priority follow-up.

### Highest ROI experiments (ordered)

1. **MiniLM-L-6 def rerank on scaffold, top-50 candidates** — establishes
   baseline cross-encoder lift.
2. **TinyBERT and MiniLM-L-12 on same setup** — model selection.
3. **Scaffold vs scaffold+body** — representation ablation.
4. **Top-20 vs top-50 vs top-100 candidates** — budget tradeoff.
5. **Cross-encoder score as LGBM feature vs standalone** — fusion test.
6. **Before vs after graph expansion** — timing test.

---

## 8. Experimental Results & Model Decision

### Bakeoff results (python-rich, 30 queries, 14,803 defs)

Two runs were executed: K=50 (20 files) and K=200 (66 files).

| Metric | TinyBERT-L2 (K=200) | MiniLM-L6 (K=200) | MiniLM-L12 (K=200) |
|--------|:-------------------:|:------------------:|:-------------------:|
| R@10 | 0.062 | 0.062 | 0.065 |
| R@20 | 0.088 | 0.095 | 0.089 |
| R@50 | 0.150 | 0.146 | 0.140 |
| NDCG@10 | 0.158 | 0.173 | 0.177 |
| NDCG@20 | 0.178 | **0.204** | 0.199 |
| Baseline R@50 (no rerank) | 0.051 | 0.051 | 0.051 |
| Pool recall | 0.379 | 0.379 | 0.379 |
| Latency mean | 831ms | 1,767ms | 2,309ms |
| Latency p95 | 932ms | 3,144ms | 3,792ms |

All three models deliver ~3x recall lift over the unranked baseline
(R@50 from 5.1% → ~14–15%), confirming cross-encoder reranking adds
real value even with a weak first-stage proxy.

Pool recall (37.9%) is the binding constraint — over 60% of ground-truth
defs never enter the candidate set. Replacing the term-overlap proxy with
BM25/Tantivy first-stage retrieval is the highest-leverage improvement.

### Decision: `cross-encoder/ms-marco-MiniLM-L-6-v2`

**Chosen model for integration into the CodeRecon reranking pipeline.**

Reasoning:

1. **Highest NDCG@20 outright** (0.204 vs 0.199 for L-12, 0.178 for
   TinyBERT). NDCG@20 best reflects the practical window — users inspect
   roughly the first 10–20 results.
2. **L-12 adds negligible quality.** +0.004 NDCG@10 over L-6 while
   costing 31% more latency (2,309ms vs 1,767ms mean). The gap is not
   statistically meaningful at n=30 queries.
3. **Latency is acceptable.** At production candidate counts (~50–100
   defs, not 200), MiniLM-L-6 should run in ~300–500ms on CPU — well
   within the 1s budget.
4. **TinyBERT is the fallback.** If latency constraints tighten (e.g.,
   embedded/edge deployment), TinyBERT at 831ms/200 candidates (~120ms
   for 50 candidates) trades ~10% NDCG for 2–3x speed.
5. **Fine-tuning path.** MiniLM-L-6's 22.7M parameters are practical to
   fine-tune on lab-generated (query, scaffold, label) triples. This is
   the long-term quality lever.

---

## Experiment Design

This experiment (`cross_encoder_rerank`) follows the SPLADE bakeoff
pattern:

1. Load indexed repos from `.recon/index.db` via `data_manifest` helpers.
2. Build scaffolds using `splade_bakeoff.scaffold` (shared code).
3. Load PR ground truth (queries + touched objects).
4. For each model × candidate count × representation:
   - First-stage retrieval simulation (BM25 scores from existing signals
     or SPLADE vectors from bakeoff).
   - Cross-encoder rerank of top-K candidates.
   - Measure def Recall@10, @20, @50 and NDCG@10, @20.
5. Write per-query results to parquet + aggregate metrics to JSON.

### CLI

```
recon-lab ce-bakeoff [--repo ID] [--model KEY] [--top-k N] [--max-queries N]
```

### Output

```
${workspace}/experiments/cross_encoder_rerank/
├── metrics.json          # aggregate per-model metrics
├── per_query.parquet     # per-query breakdown
└── latency_profile.json  # detailed timing per stage
```
