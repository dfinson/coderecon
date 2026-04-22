"""One-shot memory profiler: replay what a collect worker does, step by step."""
from __future__ import annotations

import asyncio
import gc
import json
import os
import resource
import sys
from pathlib import Path

def rss_mb() -> float:
    """Current RSS in MB."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def step(label: str) -> None:
    gc.collect()
    print(f"{label:45s}  RSS={rss_mb():8.1f} MB")


def main() -> None:
    repo_id = sys.argv[1] if len(sys.argv) > 1 else "cpp-abseil_pr1458"
    workspace = Path(os.path.expanduser("~/.recon/recon-lab"))
    data_dir = workspace / "data" / repo_id

    # Load manifest
    manifest = json.loads((data_dir / "manifest.json").read_text())
    instance_clone = Path(manifest["clone_dir"])

    # Resolve main clone dir (owns .recon/index.db) using the lab helper
    from cpl_lab.data_manifest import main_clone_dir_for_dir
    main_clone = main_clone_dir_for_dir(data_dir, workspace / "clones")
    if main_clone is None:
        print(f"Could not find main clone for {repo_id}"); return
    print(f"main_clone: {main_clone}")
    print(f"instance_clone: {instance_clone}")

    step("0. baseline (after imports)")

    # ── Step 1: AppContext construction ──
    from coderecon.mcp.context import AppContext
    step("1a. imported AppContext")

    recon_dir = main_clone / ".recon"
    ctx = AppContext.standalone(
        repo_root=instance_clone,
        db_path=recon_dir / "index.db",
        tantivy_path=recon_dir / "tantivy",
        worktree_name=instance_clone.name,
    )
    step("1b. AppContext.standalone()")

    loop = asyncio.new_event_loop()
    loop.run_until_complete(ctx.coordinator.load_existing())
    step("1c. coordinator.load_existing()")

    # ── Step 2: Parse GT to get a query ──
    gt_dir = data_dir / "ground_truth"
    queries_file = gt_dir / "queries.jsonl"
    if queries_file.exists():
        lines = queries_file.read_text().splitlines()
        q = json.loads(lines[0])
        query_text = q.get("query_text", q.get("query", ""))
    else:
        print("No queries.jsonl found, aborting"); return

    step("2. parsed GT query")
    print(f"   query: {query_text[:80]}...")

    # ── Step 3: Individual harvesters ──
    from coderecon.mcp.tools.recon.harvesters import (
        _harvest_term_match,
        _harvest_splade,
        _harvest_explicit,
        _harvest_graph,
        _harvest_imports,
    )
    from coderecon.mcp.tools.recon.merge import (
        _enrich_candidates,
        _expand_via_coverage,
        _merge_candidates,
    )
    from coderecon.mcp.tools.recon.raw_signals import parse_task
    step("3a. imported harvesters")

    parsed = parse_task(query_text)
    step("3b. parse_task()")

    term = loop.run_until_complete(_harvest_term_match(ctx, parsed))
    step(f"3c. _harvest_term_match  ({len(term)} cands)")

    splade = loop.run_until_complete(_harvest_splade(ctx, parsed))
    step(f"3d. _harvest_splade      ({len(splade)} cands)")

    explicit = loop.run_until_complete(_harvest_explicit(ctx, parsed))
    step(f"3e. _harvest_explicit    ({len(explicit)} cands)")

    merged = _merge_candidates(term, splade)
    merged = _merge_candidates(merged, explicit)
    step(f"3f. merged B-S-D         ({len(merged)} cands)")

    graph = loop.run_until_complete(_harvest_graph(ctx, merged, parsed))
    merged = _merge_candidates(merged, graph)
    step(f"3g. _harvest_graph       ({len(graph)} new, {len(merged)} total)")

    imports = loop.run_until_complete(_harvest_imports(ctx, merged, parsed))
    merged = _merge_candidates(merged, imports)
    step(f"3h. _harvest_imports     ({len(imports)} new, {len(merged)} total)")

    loop.run_until_complete(_enrich_candidates(ctx, merged))
    step(f"3i. _enrich_candidates   ({len(merged)} cands)")

    cov = loop.run_until_complete(_expand_via_coverage(ctx, merged))
    if cov:
        merged.update(cov)
        loop.run_until_complete(_enrich_candidates(ctx, cov))
    step(f"3j. _expand_via_coverage ({len(cov) if cov else 0} new)")

    # ── Step 4: Cross-encoder scoring ──
    from coderecon.mcp.tools.recon.pipeline import _score_cross_encoder_tiny, _fetch_scaffolds, _build_ce_documents
    step("4a. imported cross-encoder scorer")

    # Build candidate dicts like the real pipeline does
    cand_dicts = []
    for uid, cand in merged.items():
        if cand.def_fact is None:
            continue
        cand_dicts.append({
            "def_uid": uid,
            "path": cand.file_path or "",
            "name": cand.def_fact.name,
            "kind": cand.def_fact.kind,
        })
    step(f"4b. built {len(cand_dicts)} cand dicts")

    _score_cross_encoder_tiny(cand_dicts, query_text, ctx.coordinator.db)
    step(f"4c. _score_cross_encoder_tiny done")

    # ── Step 5: Multi-query accumulation ──
    # Run a few more queries to see if memory grows
    n_extra = min(5, len(lines) - 1)
    for i in range(1, 1 + n_extra):
        qi = json.loads(lines[i])
        qt = qi.get("query_text", qi.get("query", ""))
        from coderecon.mcp.tools.recon.raw_signals import raw_signals_pipeline
        loop.run_until_complete(raw_signals_pipeline(ctx, qt))
        step(f"5.{i}. query {i+1} done")

    print(f"\nPeak RSS: {rss_mb():.1f} MB")


if __name__ == "__main__":
    main()
