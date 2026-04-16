"""Signal generalization experiment using ACTUAL ranker features.

Runs raw_signals_pipeline (the exact pipeline the ranker trains on) for
5 annotated SWE-bench instances, then compares feature vectors between:
  - PATCH defs: defs in files changed by the patch
  - CONTEXT defs: defs in files identified as needed-to-read
  - NEGATIVE defs: everything else returned by the pipeline

Uses real LLM-generated Q_FULL queries with seeds and pins from GT data.
"""

import asyncio
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# Ensure coderecon importable
sys.path.insert(0, "/home/dave01/wsl-repos/coderecon/src")

from coderecon.mcp.context import AppContext
from coderecon.mcp.tools.recon.raw_signals import raw_signals_pipeline

CLONES = Path("/home/dave01/.recon/recon-lab/clones/instances")
DATA = Path("/home/dave01/.recon/recon-lab/data")

# patch_files = changed in diff; context_files = needed-to-read (agent-annotated)
ANNOTATIONS = {
    "astropy__astropy_14702": {
        "patch_files": [
            "astropy/io/votable/tree.py",
            "astropy/io/votable/tests/vo_test.py",
        ],
        "context_files": [
            "astropy/table/table.py",
            "astropy/io/votable/table.py",
        ],
    },
    "astropy__astropy_14578": {
        "patch_files": [
            "astropy/io/fits/column.py",
            "astropy/io/fits/tests/test_connect.py",
            "astropy/io/fits/tests/test_table.py",
        ],
        "context_files": [
            "astropy/io/fits/connect.py",
            "astropy/io/fits/fitsrec.py",
            "astropy/io/fits/hdu/table.py",
        ],
    },
    "astropy__astropy_13734": {
        "patch_files": [
            "astropy/io/ascii/fixedwidth.py",
            "astropy/io/ascii/tests/test_fixedwidth.py",
        ],
        "context_files": [
            "astropy/io/ascii/core.py",
            "astropy/io/ascii/basic.py",
            "astropy/io/ascii/ipac.py",
        ],
    },
    "astropy__astropy_13075": {
        "patch_files": [
            "astropy/cosmology/io/__init__.py",
            "astropy/cosmology/io/tests/test_.py",
            "astropy/cosmology/tests/test_connect.py",
        ],
        "context_files": [
            "astropy/cosmology/connect.py",
            "astropy/cosmology/io/table.py",
            "astropy/cosmology/io/ecsv.py",
            "astropy/cosmology/parameter.py",
            "astropy/cosmology/core.py",
        ],
    },
    "astropy__astropy_13438": {
        "patch_files": [
            "astropy/table/jsviewer.py",
            "astropy/table/tests/test_jsviewer.py",
        ],
        "context_files": [
            "astropy/table/__init__.py",
        ],
    },
}


def load_query(instance_id: str) -> dict:
    """Load Q_FULL query from GT data."""
    qf = DATA / instance_id / "ground_truth" / "queries.json"
    with open(qf) as f:
        data = json.load(f)
    for q in data["queries"]:
        if q.get("query_type") == "Q_FULL":
            return q
    # Fallback to first query
    return data["queries"][0]


async def run_pipeline(instance_id: str) -> dict:
    """Run raw_signals_pipeline for one instance, return tagged candidates."""
    worktree = CLONES / instance_id
    recon_dir = worktree / ".recon"

    ctx = AppContext.standalone(
        repo_root=worktree,
        db_path=recon_dir / "index.db",
        tantivy_path=recon_dir / "tantivy",
    )
    await ctx.coordinator.load_existing()

    query_data = load_query(instance_id)
    query_text = query_data["query_text"]
    seeds = query_data.get("seeds", [])
    pins = query_data.get("pins", [])

    result = await raw_signals_pipeline(
        ctx, query_text, seeds=seeds or None, pins=pins or None,
    )

    return result


def classify_candidate(cand: dict, patch_files: set, context_files: set) -> str:
    """Classify a candidate as patch/context/negative."""
    path = cand.get("path", "")
    if path in patch_files:
        return "patch"
    elif path in context_files:
        return "context"
    else:
        return "negative"


# The feature keys that matter for ranking (numeric/boolean signals)
BOOL_FEATURES = [
    "has_docstring", "has_decorators", "has_return_type",
    "has_parent_scope", "is_test", "is_barrel", "is_endpoint",
    "shares_file_with_seed", "is_callee_of_top", "is_imported_by_top",
    "same_package", "from_coverage",
]

NUM_FEATURES = [
    "object_size_lines", "path_depth", "nesting_depth", "hub_score",
    "test_coverage_count", "term_match_count", "term_total_matches",
    "lex_hit_count", "bm25_file_score", "graph_seed_rank",
    "retriever_hits", "seed_path_distance", "package_distance",
]

CAT_FEATURES = [
    "graph_edge_type", "symbol_source", "import_direction",
    "graph_caller_max_tier",
]


def safe_num(v):
    """Convert to float, treating None/missing as 0."""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def safe_bool(v):
    """Convert to 0/1."""
    return 1 if v else 0


def main():
    print("Signal Generalization Experiment — ACTUAL RANKER FEATURES")
    print("=" * 60)
    print()

    all_tagged = {}  # instance_id -> {patch: [...], context: [...], negative: [...]}

    for inst_id, ann in ANNOTATIONS.items():
        print(f"Running raw_signals_pipeline for {inst_id}...")
        patch_set = set(ann["patch_files"])
        context_set = set(ann["context_files"])

        result = asyncio.run(run_pipeline(inst_id))

        candidates = result.get("candidates", [])
        diag = result.get("diagnostics", {})
        print(f"  candidates={len(candidates)}  elapsed={diag.get('elapsed_ms', '?')}ms")

        tagged = {"patch": [], "context": [], "negative": []}
        for cand in candidates:
            label = classify_candidate(cand, patch_set, context_set)
            tagged[label].append(cand)

        print(f"  patch={len(tagged['patch'])}  context={len(tagged['context'])}  negative={len(tagged['negative'])}")
        all_tagged[inst_id] = tagged

    # ── Per-instance detail ──
    for inst_id, tagged in all_tagged.items():
        print(f"\n{'━' * 80}")
        print(f"  {inst_id}")
        print(f"{'━' * 80}")
        for label in ["patch", "context"]:
            if not tagged[label]:
                continue
            print(f"\n  [{label.upper()} candidates retrieved]")
            for c in tagged[label][:10]:
                flags = []
                if c.get("same_package"): flags.append("same_pkg")
                if c.get("is_imported_by_top"): flags.append("imported_by_top")
                if c.get("is_callee_of_top"): flags.append("callee_of_top")
                if c.get("shares_file_with_seed"): flags.append("seed_file")
                if c.get("from_coverage"): flags.append("coverage")
                edge = c.get("graph_edge_type", "")
                imp = c.get("import_direction", "")
                sym = c.get("symbol_source", "")
                if edge and edge != "none": flags.append(f"graph:{edge}")
                if imp and imp != "none": flags.append(f"imp:{imp}")
                if sym and sym != "none": flags.append(f"sym:{sym}")
                print(f"    {c['path']:<50} {c['kind']:<10} {c['name']:<25} ret_hits={c.get('retriever_hits',0)} pkg_dist={c.get('package_distance','?')} [{', '.join(flags)}]")
        # Show a few negatives for contrast
        print(f"\n  [NEGATIVE sample (first 5)]")
        for c in tagged["negative"][:5]:
            flags = []
            if c.get("same_package"): flags.append("same_pkg")
            edge = c.get("graph_edge_type", "")
            if edge and edge != "none": flags.append(f"graph:{edge}")
            print(f"    {c['path']:<50} {c['kind']:<10} {c['name']:<25} ret_hits={c.get('retriever_hits',0)} pkg_dist={c.get('package_distance','?')}")

    # ── Aggregate signal comparison ──
    by_label = defaultdict(list)
    for tagged in all_tagged.values():
        for label in ["patch", "context", "negative"]:
            by_label[label].extend(tagged[label])

    print(f"\n{'=' * 100}")
    print(f"  AGGREGATE FEATURE COMPARISON: PATCH={len(by_label['patch'])}  CONTEXT={len(by_label['context'])}  NEGATIVE={len(by_label['negative'])}")
    print(f"{'=' * 100}")

    # Boolean features
    print(f"\n  {'BOOLEAN FEATURES':<40} {'PATCH':>10} {'CONTEXT':>10} {'NEGATIVE':>10}  {'C→?':>8}")
    print(f"  {'-' * 82}")
    patch_closer = 0
    neg_closer = 0
    ties = 0

    for key in BOOL_FEATURES:
        row = {}
        for label in ["patch", "context", "negative"]:
            vals = by_label[label]
            if vals:
                row[label] = sum(safe_bool(v.get(key)) for v in vals) / len(vals)
            else:
                row[label] = 0
        p, c, n = row["patch"], row["context"], row["negative"]
        dp, dn = abs(c - p), abs(c - n)
        if dp < dn:
            closer = "PATCH"
            patch_closer += 1
        elif dp > dn:
            closer = "NEG"
            neg_closer += 1
        else:
            closer = "TIE"
            ties += 1
        print(f"  {key:<40} {p:>9.0%} {c:>10.0%} {n:>10.0%}  {closer:>8}")

    # Numeric features
    print(f"\n  {'NUMERIC FEATURES':<40} {'PATCH':>10} {'CONTEXT':>10} {'NEGATIVE':>10}  {'C→?':>8}")
    print(f"  {'-' * 82}")

    for key in NUM_FEATURES:
        row = {}
        for label in ["patch", "context", "negative"]:
            vals = by_label[label]
            if vals:
                row[label] = sum(safe_num(v.get(key)) for v in vals) / len(vals)
            else:
                row[label] = 0
        p, c, n = row["patch"], row["context"], row["negative"]
        dp, dn = abs(c - p), abs(c - n)
        if dp < dn:
            closer = "PATCH"
            patch_closer += 1
        elif dp > dn:
            closer = "NEG"
            neg_closer += 1
        else:
            closer = "TIE"
            ties += 1
        print(f"  {key:<40} {p:>10.2f} {c:>10.2f} {n:>10.2f}  {closer:>8}")

    # Categorical features (show mode)
    print(f"\n  {'CATEGORICAL FEATURES':<40} {'PATCH mode':>20} {'CONTEXT mode':>20} {'NEG mode':>20}")
    print(f"  {'-' * 102}")
    for key in CAT_FEATURES:
        for label in ["patch", "context", "negative"]:
            vals = [v.get(key, "none") or "none" for v in by_label[label]]
            if vals:
                from collections import Counter
                mode = Counter(vals).most_common(1)[0]
            else:
                mode = ("none", 0)
            if label == "patch":
                pm = f"{mode[0]}({mode[1]})"
            elif label == "context":
                cm = f"{mode[0]}({mode[1]})"
            else:
                nm = f"{mode[0]}({mode[1]})"
        print(f"  {key:<40} {pm:>20} {cm:>20} {nm:>20}")

    total_signals = patch_closer + neg_closer
    print(f"\n{'=' * 100}")
    print(f"  RESULT: Context closer to PATCH on {patch_closer}/{total_signals} signals, "
          f"closer to NEGATIVE on {neg_closer}/{total_signals}, ties={ties}")
    print(f"{'=' * 100}")

    if patch_closer > neg_closer:
        print("\n  CONCLUSION: On the actual ranker features, context files share")
        print("  signal patterns with patch files. A ranker trained on patch-only GT")
        print("  learns feature patterns that generalize to needed-context files.")
    else:
        print("\n  CONCLUSION: On the actual ranker features, context files do NOT")
        print("  reliably resemble patch files. Patch-only GT may be insufficient.")


if __name__ == "__main__":
    main()
