# Streaming / Async Pipeline Overlap Assessment

**Status:** REJECT — Not beneficial on CPU-only  
**Date:** 2026-04-13  
**Tier 4 #10 from red-team audit**

## Question

Would streaming / async pipeline overlap between indexing phases provide a
meaningful speedup, or would it compete with resources already consumed by
prior phases?

## Method

1. Mapped all 6 pipeline phases with resource profiles (CPU-bound vs I/O-bound)
2. Measured wall-clock time, peak RSS for each phase on spdlog (169 files, 3302 defs, 20 CPU cores)
3. Ran a direct concurrent overlap test: ONNX encoding + numpy matmul simultaneously (threaded) vs sequentially

## Phase Resource Profiles

| Phase | Operation | Time (spdlog) | Resource | Bottleneck |
|-------|-----------|---------------|----------|------------|
| 0 | Structural extraction | — | CPU (ProcessPoolExecutor, 16 workers) | Multi-core saturating |
| 1 | Tantivy lexical indexing | — | Disk I/O | Low CPU |
| 1.5 | Import resolution (DB) | — | SQLite I/O | Low CPU |
| 2 | SPLADE ONNX encoding | ~15.7s est. | CPU (ONNX intra-op threads) | All cores saturated |
| 3 | Semantic resolution (SPLADE+CE) | ~0.5s | CPU (ONNX) | ONNX session |
| 4 | Semantic neighbors (matmul) | ~1.1s | CPU (numpy/BLAS) | All cores via BLAS |
| 5 | Doc chunk encoding | ~0.2s | CPU (ONNX) | Same ONNX session |

## Measured Timings (spdlog, 3302 defs)

```
SPLADE model load                     0.78s   +92 MB RSS
SPLADE encode 500 texts (bs=16)       2.37s   +243 MB RSS
  → Estimated full encode (3302):    15.7s
Load vectors (JSON parse)             0.27s
Load vectors (binary cache, cold)     0.37s
Load vectors (binary cache, warm)     0.34s
Cross-encoder model load              0.26s
Cross-encoder score 64 pairs          0.19s
Build dense matrix (3299×4135)        0.22s   54.6 MB
Block matmul + threshold              0.87s
Incremental 50-row matmul             0.14s
Doc chunk encode (50 chunks)          0.22s
Sparse dot-product scan (1 query)     0.03s
Peak RSS                            653 MB
```

## Concurrent Overlap Test

Ran ONNX encoding (3 rounds × 100 texts) + numpy matmul (3 rounds × full matrix)
both sequentially and in parallel threads:

```
Sequential (ONNX then numpy):  2.06s
Concurrent (ONNX || numpy):    2.17s
Speedup: 0.95×
```

**Result: concurrent execution is actually 5% SLOWER than sequential.**

Both ONNX (intra-op thread pool) and numpy/BLAS saturate all available CPU
cores. Running them simultaneously causes thread contention, cache thrashing,
and context-switching overhead.

## Analysis

### Phases that COULD safely overlap (I/O ↔ CPU)
- Phase 1 (Tantivy disk I/O) with Phase 2 (ONNX encoding)
- Phase 1.5 (SQLite queries) with Phase 2 (ONNX encoding)

These are already fast (<1s) and would save at most ~0.5s on a 15+ second
pipeline. The complexity of streaming partial results between phases is not
justified.

### Phases that CANNOT overlap (CPU ↔ CPU)
- Phase 0 (multi-process extraction) ↔ Phase 2 (ONNX) — both saturate all cores
- Phase 2 (ONNX encoding) ↔ Phase 4 (numpy matmul) — **confirmed 0.95× by test**
- Phase 2 ↔ Phase 5 (doc chunks) — same ONNX session, would serialize anyway
- Phase 3 (CE scoring) ↔ Phase 4 — both CPU-intensive

### Memory pressure
At 653 MB peak RSS for a 3K-def repo, running two CPU-heavy phases
simultaneously would push RSS higher while providing no wall-clock benefit.

## Conclusion

**Streaming / async pipeline overlap is NOT beneficial for CPU-only inference.**

The pipeline is dominated by Phase 2 (SPLADE encoding, ~15.7s) which alone
saturates all CPU cores via ONNX intra-op parallelism. No other phase can
meaningfully overlap with it.

The only viable speedup paths remain:
1. **GPU offload** — moves ONNX off CPU, freeing cores for numpy/Phase 0
2. **Incremental indexing** — already implemented, skips unchanged defs entirely
3. **Binary vector cache** — already implemented, saves ~0.3s per load

If GPU becomes available, revisit: GPU-bound ONNX + CPU-bound numpy matmul
would be a genuine overlap opportunity.
