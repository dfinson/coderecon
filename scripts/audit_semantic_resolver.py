#!/usr/bin/env python3
"""Audit the semantic resolver's index-time edges for quality.

Runs against a fresh index.db and reports:
  1. Edge counts by tier / resolution method
  2. Name-match rate (does the resolved target name match the ref token?)
  3. CE score distributions (p25 / p50 / p75 / p90 / max)
  4. Same-file vs cross-file breakdown
  5. Random sample of resolved edges for spot-checking

Usage:
    # First index fresh:
    cd /path/to/repo && recon register --reindex
    # Then audit:
    python scripts/audit_semantic_resolver.py /path/to/repo/.recon/index.db
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np
from sqlmodel import Session, col, create_engine, select, text

# Add src to path so we can import models
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from coderecon.index.models import (
    DefFact,
    File,
    MemberAccessFact,
    ReceiverShapeFact,
    RefFact,
)


def _engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


def _header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _pct(arr: np.ndarray) -> str:
    if len(arr) == 0:
        return "(empty)"
    pcts = np.percentile(arr, [25, 50, 75, 90, 99])
    return (
        f"n={len(arr):,}  "
        f"p25={pcts[0]:.2f}  p50={pcts[1]:.2f}  p75={pcts[2]:.2f}  "
        f"p90={pcts[3]:.2f}  p99={pcts[4]:.2f}  "
        f"max={arr.max():.2f}  min={arr.min():.2f}"
    )


def audit_refs(session: Session) -> None:
    _header("RefFact — Reference Resolution")

    all_refs = list(session.exec(select(RefFact)).all())
    refs_by_role = Counter(r.role for r in all_refs)
    print(f"Total RefFacts: {len(all_refs):,}")
    for role, cnt in refs_by_role.most_common():
        print(f"  {role}: {cnt:,}")

    # Only look at REFERENCE role (not DEFINITION/IMPORT/EXPORT)
    refs = [r for r in all_refs if r.role == "reference"]
    print(f"\nReferences (role=reference): {len(refs):,}")

    tier_counts = Counter(r.ref_tier for r in refs)
    for tier, cnt in tier_counts.most_common():
        print(f"  {tier}: {cnt:,}  ({100*cnt/len(refs):.1f}%)")

    # Semantic refs
    semantic = [r for r in refs if r.ref_tier == "semantic"]
    print(f"\nSemantic refs: {len(semantic):,}")
    if not semantic:
        print("  (none — semantic resolver produced zero ref edges)")
        return

    # Get all def_uids we need
    target_uids = {r.target_def_uid for r in semantic if r.target_def_uid}
    source_uids = set()  # refs don't have a source def_uid but have file_id
    defs = {}
    if target_uids:
        for batch_start in range(0, len(target_uids), 500):
            batch = list(target_uids)[batch_start:batch_start + 500]
            rows = list(session.exec(
                select(DefFact).where(col(DefFact.def_uid).in_(batch))
            ).all())
            for d in rows:
                defs[d.def_uid] = d

    # File map
    file_ids = {r.file_id for r in semantic} | {d.file_id for d in defs.values()}
    files = {}
    if file_ids:
        for batch_start in range(0, len(file_ids), 500):
            batch = list(file_ids)[batch_start:batch_start + 500]
            rows = list(session.exec(
                select(File).where(col(File.id).in_(batch))
            ).all())
            for f in rows:
                if f.id is not None:
                    files[f.id] = f

    # CE score distribution (stored in certainty field as string)
    scores = []
    for r in semantic:
        try:
            scores.append(float(r.certainty))
        except (ValueError, TypeError):
            pass
    scores_arr = np.array(scores) if scores else np.array([])
    print(f"\n  CE score distribution: {_pct(scores_arr)}")

    # Name match analysis
    exact_match = 0
    case_insensitive_match = 0
    substring_match = 0
    no_match = 0
    same_file = 0
    cross_file = 0

    for r in semantic:
        d = defs.get(r.target_def_uid) if r.target_def_uid else None
        if d is None:
            continue
        # Same-file check
        if r.file_id == d.file_id:
            same_file += 1
        else:
            cross_file += 1
        # Name match
        ref_text = r.token_text.strip()
        def_name = d.name.strip()
        if ref_text == def_name:
            exact_match += 1
        elif ref_text.lower() == def_name.lower():
            case_insensitive_match += 1
        elif ref_text.lower() in def_name.lower() or def_name.lower() in ref_text.lower():
            substring_match += 1
        else:
            no_match += 1

    total_checked = exact_match + case_insensitive_match + substring_match + no_match
    print(f"\n  Name match (ref token vs target def name):")
    if total_checked:
        print(f"    Exact:           {exact_match:,}  ({100*exact_match/total_checked:.1f}%)")
        print(f"    Case-insensitive:{case_insensitive_match:,}  ({100*case_insensitive_match/total_checked:.1f}%)")
        print(f"    Substring:       {substring_match:,}  ({100*substring_match/total_checked:.1f}%)")
        print(f"    No match:        {no_match:,}  ({100*no_match/total_checked:.1f}%)")
    print(f"\n  Same-file: {same_file:,}   Cross-file: {cross_file:,}")

    # Sample resolved edges
    print(f"\n  Sample semantic ref edges (up to 20):")
    rng = np.random.default_rng(42)
    sample_indices = rng.choice(len(semantic), size=min(20, len(semantic)), replace=False)
    for idx in sorted(sample_indices):
        r = semantic[idx]
        d = defs.get(r.target_def_uid) if r.target_def_uid else None
        src_file = files.get(r.file_id)
        src_path = src_file.path if src_file else "?"
        if d:
            tgt_file = files.get(d.file_id)
            tgt_path = tgt_file.path if tgt_file else "?"
            try:
                score = float(r.certainty)
                score_str = f"{score:.2f}"
            except (ValueError, TypeError):
                score_str = r.certainty
            match = "EXACT" if r.token_text == d.name else (
                "CASE" if r.token_text.lower() == d.name.lower() else (
                    "SUB" if r.token_text.lower() in d.name.lower() or d.name.lower() in r.token_text.lower()
                    else "NONE"
                )
            )
            same = "same-file" if r.file_id == d.file_id else "CROSS"
            print(f"    [{score_str}] [{match}] [{same}]  "
                  f"'{r.token_text}' (L{r.start_line} {src_path}) "
                  f"→ {d.kind}:{d.name} ({tgt_path})")
        else:
            print(f"    [?] '{r.token_text}' → target def not found ({r.target_def_uid})")


def audit_accesses(session: Session) -> None:
    _header("MemberAccessFact — Access Resolution")

    all_accesses = list(session.exec(select(MemberAccessFact)).all())
    print(f"Total MemberAccessFacts: {len(all_accesses):,}")

    method_counts = Counter(a.resolution_method for a in all_accesses)
    for method, cnt in method_counts.most_common():
        label = method or "(unresolved)"
        print(f"  {label}: {cnt:,}  ({100*cnt/len(all_accesses):.1f}%)" if all_accesses else "")

    semantic = [a for a in all_accesses if a.resolution_method == "semantic"]
    print(f"\nSemantic accesses: {len(semantic):,}")
    if not semantic:
        print("  (none — semantic resolver produced zero access edges)")
        return

    # Get target defs
    target_uids = {a.final_target_def_uid for a in semantic if a.final_target_def_uid}
    defs = {}
    if target_uids:
        for batch_start in range(0, len(target_uids), 500):
            batch = list(target_uids)[batch_start:batch_start + 500]
            rows = list(session.exec(
                select(DefFact).where(col(DefFact.def_uid).in_(batch))
            ).all())
            for d in rows:
                defs[d.def_uid] = d

    # File map
    file_ids = {a.file_id for a in semantic} | {d.file_id for d in defs.values()}
    files = {}
    if file_ids:
        for batch_start in range(0, len(file_ids), 500):
            batch = list(file_ids)[batch_start:batch_start + 500]
            rows = list(session.exec(
                select(File).where(col(File.id).in_(batch))
            ).all())
            for f in rows:
                if f.id is not None:
                    files[f.id] = f

    # CE confidence distribution
    scores = [a.resolution_confidence for a in semantic if a.resolution_confidence is not None]
    scores_arr = np.array(scores) if scores else np.array([])
    print(f"\n  CE confidence distribution: {_pct(scores_arr)}")

    # Name match: final_member vs target def name
    exact = 0
    case_match = 0
    sub_match = 0
    no_match = 0
    same_file = 0
    cross_file = 0

    for a in semantic:
        d = defs.get(a.final_target_def_uid) if a.final_target_def_uid else None
        if d is None:
            continue
        if a.file_id == d.file_id:
            same_file += 1
        else:
            cross_file += 1
        fm = a.final_member.strip()
        dn = d.name.strip()
        if fm == dn:
            exact += 1
        elif fm.lower() == dn.lower():
            case_match += 1
        elif fm.lower() in dn.lower() or dn.lower() in fm.lower():
            sub_match += 1
        else:
            no_match += 1

    total = exact + case_match + sub_match + no_match
    print(f"\n  Name match (final_member vs target def name):")
    if total:
        print(f"    Exact:           {exact:,}  ({100*exact/total:.1f}%)")
        print(f"    Case-insensitive:{case_match:,}  ({100*case_match/total:.1f}%)")
        print(f"    Substring:       {sub_match:,}  ({100*sub_match/total:.1f}%)")
        print(f"    No match:        {no_match:,}  ({100*no_match/total:.1f}%)")
    print(f"\n  Same-file: {same_file:,}   Cross-file: {cross_file:,}")

    # Samples
    print(f"\n  Sample semantic access edges (up to 20):")
    rng = np.random.default_rng(42)
    sample_indices = rng.choice(len(semantic), size=min(20, len(semantic)), replace=False)
    for idx in sorted(sample_indices):
        a = semantic[idx]
        d = defs.get(a.final_target_def_uid) if a.final_target_def_uid else None
        src_file = files.get(a.file_id)
        src_path = src_file.path if src_file else "?"
        if d:
            tgt_file = files.get(d.file_id)
            tgt_path = tgt_file.path if tgt_file else "?"
            conf = f"{a.resolution_confidence:.2f}" if a.resolution_confidence else "?"
            match = "EXACT" if a.final_member == d.name else (
                "CASE" if a.final_member.lower() == d.name.lower() else (
                    "SUB" if a.final_member.lower() in d.name.lower() or d.name.lower() in a.final_member.lower()
                    else "NONE"
                )
            )
            same = "same-file" if a.file_id == d.file_id else "CROSS"
            print(f"    [{conf}] [{match}] [{same}]  "
                  f"'{a.full_expression}' .{a.final_member} (L{a.start_line} {src_path}) "
                  f"→ {d.kind}:{d.name} ({tgt_path})")
        else:
            print(f"    [?] '{a.full_expression}' → target def not found")


def audit_shapes(session: Session) -> None:
    _header("ReceiverShapeFact — Shape Resolution")

    all_shapes = list(session.exec(select(ReceiverShapeFact)).all())
    print(f"Total ReceiverShapeFacts: {len(all_shapes):,}")

    resolved = [s for s in all_shapes if s.best_match_type is not None]
    unresolved = [s for s in all_shapes if s.best_match_type is None]
    print(f"  Resolved: {len(resolved):,}")
    print(f"  Unresolved: {len(unresolved):,}")

    if not resolved:
        print("  (none — semantic resolver produced zero shape edges)")
        return

    # Confidence distribution
    scores = [s.match_confidence for s in resolved if s.match_confidence is not None]
    scores_arr = np.array(scores) if scores else np.array([])
    print(f"\n  CE confidence distribution: {_pct(scores_arr)}")

    # Sample
    print(f"\n  Sample resolved shapes (up to 15):")
    rng = np.random.default_rng(42)
    sample_indices = rng.choice(len(resolved), size=min(15, len(resolved)), replace=False)

    file_ids = {s.file_id for s in resolved}
    files = {}
    if file_ids:
        for batch_start in range(0, len(file_ids), 500):
            batch = list(file_ids)[batch_start:batch_start + 500]
            rows = list(session.exec(
                select(File).where(col(File.id).in_(batch))
            ).all())
            for f in rows:
                if f.id is not None:
                    files[f.id] = f

    for idx in sorted(sample_indices):
        s = resolved[idx]
        src_file = files.get(s.file_id)
        src_path = src_file.path if src_file else "?"
        members = s.get_observed_members()
        fields = members.get("fields", [])[:5]
        methods = members.get("methods", [])[:5]
        conf = f"{s.match_confidence:.2f}" if s.match_confidence else "?"
        print(f"    [{conf}] receiver={s.receiver_name} → {s.best_match_type}  "
              f"({src_path} L{s.start_line})  "
              f"fields={fields} methods={methods}")


def audit_overall_timing(db_path: str) -> None:
    """Check if there are any timing stats in the logs table or state."""
    _header("Summary")
    engine = _engine(db_path)
    with Session(engine) as session:
        # Total defs
        def_count = session.exec(select(DefFact)).all()
        print(f"Total DefFacts: {len(def_count):,}")

        refs = session.exec(select(RefFact)).all()
        semantic_refs = [r for r in refs if r.ref_tier == "semantic" and r.role == "reference"]
        all_reference_refs = [r for r in refs if r.role == "reference"]
        resolved_refs = [r for r in all_reference_refs if r.target_def_uid is not None]
        print(f"Total references: {len(all_reference_refs):,}")
        print(f"  Resolved (any method): {len(resolved_refs):,}  ({100*len(resolved_refs)/max(len(all_reference_refs),1):.1f}%)")
        print(f"  Resolved by semantic:  {len(semantic_refs):,}  ({100*len(semantic_refs)/max(len(all_reference_refs),1):.1f}%)")

        accesses = session.exec(select(MemberAccessFact)).all()
        semantic_acc = [a for a in accesses if a.resolution_method == "semantic"]
        resolved_acc = [a for a in accesses if a.final_target_def_uid is not None]
        print(f"\nTotal accesses: {len(accesses):,}")
        print(f"  Resolved (any method): {len(resolved_acc):,}  ({100*len(resolved_acc)/max(len(accesses),1):.1f}%)")
        print(f"  Resolved by semantic:  {len(semantic_acc):,}  ({100*len(semantic_acc)/max(len(accesses),1):.1f}%)")

        shapes = session.exec(select(ReceiverShapeFact)).all()
        resolved_shapes = [s for s in shapes if s.best_match_type is not None]
        print(f"\nTotal shapes: {len(shapes):,}")
        print(f"  Resolved: {len(resolved_shapes):,}  ({100*len(resolved_shapes)/max(len(shapes),1):.1f}%)")


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <path/to/index.db>")
        sys.exit(1)

    db_path = sys.argv[1]
    if not Path(db_path).exists():
        print(f"ERROR: {db_path} does not exist")
        sys.exit(1)

    print(f"Auditing: {db_path}")
    engine = _engine(db_path)

    with Session(engine) as session:
        audit_refs(session)
        audit_accesses(session)
        audit_shapes(session)

    audit_overall_timing(db_path)


if __name__ == "__main__":
    main()
