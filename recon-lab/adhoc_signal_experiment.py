"""Ad-hoc experiment: Do context-GT defs share signal patterns with patch-GT defs?

For 5 annotated SWE-bench instances we measure FILE-level structural signals:
  - PATCH files: files directly changed in the diff
  - CONTEXT files: files an expert identified as "needed to read"
  - RANDOM files: random sample from the same repo

We measure signals the ranker actually uses (import graph, package distance)
at the FILE level, since that's the granularity retrieval operates at.

If context files look like patch files on these signals (and unlike random),
then training on patch-only GT teaches the ranker signal patterns that
generalize to needed-context files too.
"""

import sqlite3
import random
from pathlib import Path
from collections import defaultdict

CLONES = Path("/home/dave01/.recon/recon-lab/clones/instances")

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


def compute_file_signals(cur, filepath, patch_file_paths):
    """Compute structural signals for a FILE relative to the patch files."""
    parts = filepath.split("/")
    basename = parts[-1] if parts else ""

    # 1. Is test file?
    is_test = any(p in ("tests", "test") for p in parts[:-1]) or \
              basename.startswith("test_") or basename.endswith("_test.py")

    # 2. Same package as any patch file?
    file_package = "/".join(parts[:-1])
    patch_packages = {"/".join(pf.split("/")[:-1]) for pf in patch_file_paths}
    same_package = file_package in patch_packages

    # 3. Package distance to nearest patch file
    min_dist = 999
    for pf in patch_file_paths:
        pf_parts = pf.split("/")
        common = 0
        for a, b in zip(parts, pf_parts):
            if a == b:
                common += 1
            else:
                break
        dist = (len(parts) - common) + (len(pf_parts) - common)
        min_dist = min(min_dist, dist)

    # 4. Import graph: does this file import any patch file?
    patch_set = set(patch_file_paths)
    imports_patch = cur.execute("""
        SELECT COUNT(DISTINCT i.resolved_path)
        FROM import_facts i
        JOIN files f ON i.file_id = f.id
        WHERE f.path = ? AND i.resolved_path IN ({})
    """.format(",".join("?" * len(patch_set))),
        (filepath, *patch_set)).fetchone()[0]

    # 5. Import graph: does any patch file import this file?
    imported_by_patch = cur.execute("""
        SELECT COUNT(DISTINCT f.path)
        FROM import_facts i
        JOIN files f ON i.file_id = f.id
        WHERE i.resolved_path = ? AND f.path IN ({})
    """.format(",".join("?" * len(patch_set))),
        (filepath, *patch_set)).fetchone()[0]

    # 6. Broader: does this file import any file that imports a patch file?
    # (2-hop import: file -> X -> patch_file)
    one_hop_importers = set()
    rows = cur.execute("""
        SELECT DISTINCT f.path
        FROM import_facts i
        JOIN files f ON i.file_id = f.id
        WHERE i.resolved_path IN ({})
    """.format(",".join("?" * len(patch_set))),
        list(patch_set)).fetchall()
    for (p,) in rows:
        one_hop_importers.add(p)

    imports_neighbor = cur.execute("""
        SELECT COUNT(DISTINCT i.resolved_path)
        FROM import_facts i
        JOIN files f ON i.file_id = f.id
        WHERE f.path = ? AND i.resolved_path IN ({})
    """.format(",".join("?" * len(one_hop_importers))) if one_hop_importers else "SELECT 0",
        (filepath, *one_hop_importers) if one_hop_importers else ()).fetchone()[0]

    # 7. Shared import: do this file and any patch file import the same module?
    file_imports = set(r[0] for r in cur.execute("""
        SELECT DISTINCT i.resolved_path
        FROM import_facts i
        JOIN files f ON i.file_id = f.id
        WHERE f.path = ? AND i.resolved_path IS NOT NULL
    """, (filepath,)).fetchall())

    patch_imports = set()
    for pf in patch_file_paths:
        for (rp,) in cur.execute("""
            SELECT DISTINCT i.resolved_path
            FROM import_facts i
            JOIN files f ON i.file_id = f.id
            WHERE f.path = ? AND i.resolved_path IS NOT NULL
        """, (pf,)).fetchall():
            patch_imports.add(rp)

    shared_imports = len(file_imports & patch_imports)

    # 8. Number of defs in this file
    num_defs = cur.execute("""
        SELECT COUNT(*) FROM def_facts d
        JOIN files f ON d.file_id = f.id
        WHERE f.path = ?
    """, (filepath,)).fetchone()[0]

    # 9. Total inbound references to defs in this file (popularity)
    inbound_refs = cur.execute("""
        SELECT COUNT(*) FROM ref_facts r
        WHERE r.target_def_uid IN (
            SELECT d.def_uid FROM def_facts d
            JOIN files f ON d.file_id = f.id
            WHERE f.path = ?
        ) AND r.role = 'REFERENCE'
    """, (filepath,)).fetchone()[0]

    has_any_import_link = (imports_patch > 0) or (imported_by_patch > 0)

    return {
        "path": filepath,
        "is_test": is_test,
        "same_package": same_package,
        "package_distance": min_dist,
        "imports_patch": imports_patch > 0,
        "imported_by_patch": imported_by_patch > 0,
        "has_import_link": has_any_import_link,
        "imports_neighbor_of_patch": imports_neighbor > 0,
        "shared_imports_with_patch": shared_imports,
        "num_defs": num_defs,
        "inbound_refs": inbound_refs,
    }


def run_instance(instance_id, annotation):
    """Run FILE-level analysis for one instance."""
    db_path = CLONES / instance_id / ".recon" / "index.db"
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()

    patch_files = annotation["patch_files"]
    context_files = annotation["context_files"]
    all_known = set(patch_files) | set(context_files)

    # Get all indexed files
    all_files = [r[0] for r in cur.execute(
        "SELECT path FROM files WHERE path LIKE '%.py'"
    ).fetchall()]

    # Random sample: same count as patch+context, excluding both
    other_files = [f for f in all_files if f not in all_known]
    random.seed(42)
    n_sample = len(patch_files) + len(context_files)
    random_files = random.sample(other_files, min(n_sample, len(other_files)))

    results = {"patch": [], "context": [], "random": []}

    for label, files in [("patch", patch_files), ("context", context_files), ("random", random_files)]:
        for fp in files:
            # Verify file exists in index
            exists = cur.execute("SELECT COUNT(*) FROM files WHERE path = ?", (fp,)).fetchone()[0]
            if not exists:
                print(f"  SKIP {fp} (not in index)")
                continue
            signals = compute_file_signals(cur, fp, patch_files)
            signals["label"] = label
            results[label].append(signals)

    con.close()
    return results


def print_results(all_results):
    """Print per-instance and aggregate results."""
    # Per-instance detail
    for inst_id, results in all_results.items():
        print(f"\n{'━' * 80}")
        print(f"  {inst_id}")
        print(f"{'━' * 80}")
        for label in ["patch", "context", "random"]:
            if not results[label]:
                continue
            print(f"\n  [{label.upper()}]")
            for s in results[label]:
                flags = []
                if s["same_package"]: flags.append("same_pkg")
                if s["imports_patch"]: flags.append("imports→patch")
                if s["imported_by_patch"]: flags.append("patch→imports")
                if s["imports_neighbor_of_patch"]: flags.append("imports_neighbor")
                flag_str = ", ".join(flags) if flags else "(no direct link)"
                print(f"    {s['path']:<55} dist={s['package_distance']}  shared_imp={s['shared_imports_with_patch']}  {flag_str}")

    # Aggregate
    by_label = defaultdict(list)
    for results in all_results.values():
        for label in ["patch", "context", "random"]:
            by_label[label].extend(results[label])

    bool_keys = ["same_package", "imports_patch", "imported_by_patch",
                 "has_import_link", "imports_neighbor_of_patch", "is_test"]
    num_keys = ["package_distance", "shared_imports_with_patch", "inbound_refs"]

    print(f"\n{'=' * 90}")
    print(f"  AGGREGATE SIGNAL COMPARISON ({len(all_results)} instances)")
    print(f"{'=' * 90}")
    print(f"  {'':40} {'PATCH':>10} {'CONTEXT':>10} {'RANDOM':>10}  {'C→?':>8}")
    print(f"  {'-' * 80}")

    for key in bool_keys:
        row = {}
        for label in ["patch", "context", "random"]:
            vals = by_label[label]
            row[label] = sum(1 for v in vals if v[key]) / len(vals) if vals else 0

        p, c, r = row["patch"], row["context"], row["random"]
        closer = "PATCH" if abs(c - p) < abs(c - r) else "RANDOM"
        if abs(c - p) == abs(c - r): closer = "TIE"
        print(f"  {key:<40} {p:>9.0%} {c:>10.0%} {r:>10.0%}  {closer:>8}")

    for key in num_keys:
        row = {}
        for label in ["patch", "context", "random"]:
            vals = by_label[label]
            row[label] = sum(v[key] for v in vals) / len(vals) if vals else 0

        p, c, r = row["patch"], row["context"], row["random"]
        closer = "PATCH" if abs(c - p) < abs(c - r) else "RANDOM"
        if abs(c - p) == abs(c - r): closer = "TIE"
        print(f"  {key:<40} {p:>10.1f} {c:>10.1f} {r:>10.1f}  {closer:>8}")

    print(f"\n  File counts: PATCH={len(by_label['patch'])}  CONTEXT={len(by_label['context'])}  RANDOM={len(by_label['random'])}")

    return by_label


if __name__ == "__main__":
    print("Signal Generalization Experiment (FILE-level)")
    print("=" * 50)
    print()
    print("HYPOTHESIS: Structural signals that distinguish patch files")
    print("from random files ALSO distinguish context files from random.")
    print("If true → training on patch-only GT generalizes to context GT.")
    print()

    all_results = {}
    for inst_id, ann in ANNOTATIONS.items():
        print(f"Processing {inst_id}...")
        results = run_instance(inst_id, ann)
        all_results[inst_id] = results

    by_label = print_results(all_results)

    print(f"\n{'=' * 90}")
    print("CONCLUSION")
    print(f"{'=' * 90}")
    # Count how many signals have context closer to patch
    bool_keys = ["same_package", "imports_patch", "imported_by_patch",
                 "has_import_link", "imports_neighbor_of_patch", "is_test"]
    num_keys = ["package_distance", "shared_imports_with_patch", "inbound_refs"]
    closer_patch = 0
    closer_random = 0
    for key in bool_keys + num_keys:
        for label in ["patch", "context", "random"]:
            vals = by_label[label]
            if key in bool_keys:
                v = sum(1 for x in vals if x[key]) / len(vals) if vals else 0
            else:
                v = sum(x[key] for x in vals) / len(vals) if vals else 0
            if label == "patch": p = v
            elif label == "context": c = v
            else: r = v
        if abs(c - p) < abs(c - r):
            closer_patch += 1
        elif abs(c - p) > abs(c - r):
            closer_random += 1
    print(f"\n  Signals where CONTEXT is closer to PATCH: {closer_patch}/{closer_patch + closer_random}")
    print(f"  Signals where CONTEXT is closer to RANDOM: {closer_random}/{closer_patch + closer_random}")
    if closer_patch > closer_random:
        print("\n  → CONTEXT files share signal patterns with PATCH files.")
        print("  → A ranker trained on patch-only GT learns signals that generalize.")
    else:
        print("\n  → CONTEXT files do NOT reliably share signals with PATCH files.")
        print("  → Patch-only GT may be insufficient for training generalizable rankers.")
