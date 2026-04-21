# Experiment: Tantivy-Backed SPLADE Retrieval

## Hypothesis

Storing SPLADE sparse vectors in a Tantivy inverted index (instead of SQLite
JSON blobs) will improve **query-time retrieval latency** by leveraging
Tantivy's native inverted-index structure, which is purpose-built for sparse
term lookups — at the cost of additional storage and indexing overhead.

## Background

SPLADE produces sparse vectors over a 30,522-dim vocabulary (BERT WordPiece).
Each vector has ~100-200 active dimensions with float weights.

**Current approach (SQLite JSON):**
- Vectors stored as `{term_id: weight}` JSON blobs in `splade_vecs` table
- Query: load all vectors → sparse dot product → rank
- Bulk operations (neighbors, resolver): `load_all_vectors_fast()` with
  numpy binary cache (`.splade_vecs.npz`)
- Incremental: delete/insert rows, invalidate binary cache

**Proposed approach (Tantivy inverted index):**
- Each SPLADE vector becomes a Tantivy document with term-frequency fields
- Tantivy natively supports BM25 scoring over inverted postings lists
- Query: use Tantivy's native search with SPLADE query vector as term weights
- Tantivy handles the inverted index, postings compression, and skip lists

## What This Experiment Measures

### Primary Metrics

| Metric | Description | Method |
|--------|-------------|--------|
| **Query latency** | Time from query text → ranked def_uids | Benchmark `retrieve_splade()` (p50, p95, p99) over 200 queries |
| **Indexing throughput** | Vectors/sec during encode+store | Time `encode_and_store_vectors()` on repos of varying size |
| **Storage size** | Bytes on disk for vector data | Compare SQLite + npz cache vs Tantivy index directory |
| **Incremental update cost** | Time to update vectors for N changed files | Measure delete+reinsert for 1, 10, 50 files |

### Secondary Metrics

| Metric | Description |
|--------|-------------|
| **Recall@K** | Do both backends return the same top-K results for identical queries? |
| **Score correlation** | Spearman rank correlation between SQLite and Tantivy scores |
| **Memory footprint** | Peak RSS during query and during indexing |
| **Cache warm-up** | Cold-start latency (first query after process restart) |

## Experiment Design

### Corpus

Use 5 ground-truth repos spanning different scales:

| Repo | ~Defs | ~Files | Category |
|------|-------|--------|----------|
| Small Python | 200 | 30 | Baseline |
| Medium Python | 2,000 | 150 | Typical project |
| Large Python | 10,000 | 800 | Large monorepo |
| Mixed (TS+Py) | 5,000 | 400 | Multi-language |
| Very Large | 50,000+ | 3,000+ | Stress test |

### Query Set

200 queries drawn from:
- 50 natural-language questions (from GT eval set)
- 50 function/class names (exact symbol lookup)
- 50 mixed queries (partial names + description)
- 50 adversarial queries (long, broad, or empty-ish)

### Implementation Plan

#### Phase 1: Tantivy SPLADE Index Prototype

Create `TantivySpladeIndex` class in a new module
`src/coderecon/index/_internal/indexing/splade_tantivy.py`:

```python
class TantivySpladeIndex:
    """SPLADE vector storage and retrieval via Tantivy inverted index."""

    def __init__(self, index_path: Path):
        ...

    def add_vectors(self, vectors: dict[str, dict[int, float]]) -> int:
        """Index SPLADE vectors as Tantivy documents.

        Each vector becomes a document where:
        - term IDs map to Tantivy field names (e.g., "t_12345")
        - weights are stored as term frequencies (quantized to int)
        """
        ...

    def remove_vectors(self, def_uids: list[str]) -> int:
        """Remove vectors by def_uid."""
        ...

    def query(self, query_vec: dict[int, float], limit: int) -> list[tuple[str, float]]:
        """Retrieve top-K def_uids by SPLADE score."""
        ...
```

**Key design decisions to test:**

1. **Weight encoding**: Tantivy uses integer term frequencies. SPLADE weights
   are floats (typically 0.1–5.0). Options:
   - Quantize to int (multiply by 1000, round)
   - Use Tantivy's `f64` field type with custom scoring
   - Store raw floats in a stored field, compute dot product manually

2. **Schema design**: One field per vocab term (30,522 fields) vs. a single
   text field with synthetic "tokens" like `t12345_w3500` encoding both term
   and quantized weight.

3. **Query strategy**: Tantivy's native BM25 vs. custom scoring plugin.

#### Phase 2: Benchmark Harness

```
recon-lab/experiments/tantivy_splade_bench.py
```

Runs both backends on the same corpus + query set, collects all metrics above,
outputs a comparison table.

#### Phase 3: Analysis

Produce:
- Latency histograms (SQLite vs Tantivy)
- Recall@K curves at K=10, 50, 100
- Score scatter plot with Spearman ρ
- Storage size comparison bar chart
- Indexing throughput line chart (defs vs time)

## Expected Tradeoffs

| Dimension | SQLite JSON + npz cache | Tantivy Inverted Index |
|-----------|------------------------|----------------------|
| Query latency | O(n) scan all vectors | O(q·k) postings lookup (q = query terms, k = avg postings length) |
| Cold start | Load npz mmap (~instant) | Tantivy index open (~fast) |
| Incremental | Delete rows + invalidate cache | Delete docs + re-add |
| Bulk operations | Dense matrix from cache (fast) | Must extract all vectors (slow?) |
| Storage | SQLite + npz sidecar | Tantivy directory |
| Complexity | Simple, single DB | Two storage backends to keep in sync |
| Neighbor computation | Needs dense matrix anyway | Would still need to extract all vectors for matmul |

## Decision Criteria

**Adopt Tantivy SPLADE if ALL of:**
1. Query latency p95 improves by ≥ 2× for repos with ≥ 2,000 defs
2. Recall@100 ≥ 0.99 (near-identical ranking)
3. Incremental update cost is not worse than 2× current
4. Storage overhead is ≤ 2× current

**Reject if ANY of:**
1. Bulk vector extraction (for neighbors/resolver) becomes a bottleneck
2. Keeping SQLite + Tantivy in sync adds significant complexity
3. Query latency improvement is < 1.5× (not worth the complexity)

## Timeline

1. **Prototype**: `TantivySpladeIndex` class + basic tests
2. **Benchmark harness**: Automated comparison script
3. **Run on GT corpus**: Collect metrics across 5 repos
4. **Analysis + decision**: Present results, make go/no-go call

## Open Questions

- Does Tantivy's Python binding (`tantivy-py`) support custom scoring, or
  are we limited to BM25? If BM25-only, the score correlation may diverge
  from true SPLADE dot-product.
- Can we store float weights efficiently, or does quantization to int
  degrade ranking quality?
- For bulk operations (semantic neighbors, resolver), we still need the
  full dense matrix. Would we maintain both storage backends, or extract
  from Tantivy on demand?
