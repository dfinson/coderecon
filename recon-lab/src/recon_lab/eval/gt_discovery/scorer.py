"""Scorer for GT discovery experiment.

Compares agent-accessed defs against ground truth, classifying each
as TRUE_POS, FALSE_NEG, or NOVEL_CONTEXT (accessed but not in GT).
For NOVEL_CONTEXT entries, pulls structural signals from the index
to enable downstream rule mining.
"""

from __future__ import annotations

from typing import Any

from inspect_ai.scorer import Score, Target, accuracy, scorer


def _def_key(d: dict) -> str:
    return f"{d['path']}:{d.get('kind', '')}:{d.get('name', '')}:{d.get('start_line', 0)}"


@scorer(metrics=[accuracy()])
def gt_discovery_scorer():
    """Score GT discovery by comparing traced accesses against GT.

    Output structure per sample:
    - true_positives: GT defs that were accessed
    - false_negatives: GT defs the agent missed
    - novel_context: Defs accessed but not in GT (expansion candidates)
    - structural_signals: For each novel_context def, computed signals
    - recall: len(true_pos) / len(gt)
    - precision_vs_gt: len(true_pos) / len(accessed)
    """

    async def score(state: Any, target: Target) -> Score:
        meta = state.metadata
        trace = state.store.get("access_trace", {})
        touched_uids = set(state.store.get("touched_def_uids", []))

        # GT def keys from metadata
        gt_defs = meta.get("gt_def_details", [])
        gt_keys = set(meta.get("gt_edited", []))

        # Build uid→key mappings from trace records
        uid_to_records: dict[str, dict] = {}
        for rec in trace.get("records", []):
            uid = rec.get("def_uid")
            if uid and uid not in uid_to_records:
                uid_to_records[uid] = rec

        # GT defs have path:kind:name:start_line format, trace has def_uids
        # We need to match them. Build a key for each traced def.
        traced_keys: dict[str, str] = {}  # key → uid
        for uid, rec in uid_to_records.items():
            key = f"{rec.get('path', '')}:{rec.get('kind', '')}:{rec.get('name', '')}:{rec.get('start_line', 0)}"
            traced_keys[key] = uid

        # Classify
        true_positives = []
        false_negatives = []

        for gt_def in gt_defs:
            key = _def_key(gt_def)
            if key in traced_keys:
                true_positives.append({**gt_def, "def_key": key, "def_uid": traced_keys[key]})
            else:
                false_negatives.append({**gt_def, "def_key": key})

        # Novel context: traced defs NOT in GT
        novel_context = []
        for key, uid in traced_keys.items():
            if key not in gt_keys:
                rec = uid_to_records.get(uid, {})
                novel_context.append({
                    "def_uid": uid,
                    "def_key": key,
                    "path": rec.get("path", ""),
                    "name": rec.get("name", ""),
                    "kind": rec.get("kind", ""),
                    "start_line": rec.get("start_line"),
                    "end_line": rec.get("end_line"),
                    "access_type": rec.get("access_type", ""),
                    "turn": rec.get("turn", 0),
                })

        # Structural signals for novel context
        structural_signals = _compute_structural_signals(
            novel_context, gt_defs, meta
        )

        # Metrics
        gt_count = len(gt_defs) if gt_defs else 1
        accessed_count = len(traced_keys) if traced_keys else 1
        recall = len(true_positives) / gt_count
        precision_vs_gt = len(true_positives) / accessed_count

        explanation_parts = [
            f"Recall: {recall:.2%} ({len(true_positives)}/{len(gt_defs)})",
            f"Precision vs GT: {precision_vs_gt:.2%}",
            f"Novel context: {len(novel_context)} defs",
            f"False negatives: {len(false_negatives)} defs",
            f"Turns used: {trace.get('total_turns', 0)}",
            f"Tool calls: {len(trace.get('tool_calls', []))}",
        ]

        return Score(
            value=recall,
            answer=str(len(true_positives)),
            explanation="\n".join(explanation_parts),
            metadata={
                "true_positives": true_positives,
                "false_negatives": false_negatives,
                "novel_context": novel_context,
                "structural_signals": structural_signals,
                "recall": recall,
                "precision_vs_gt": precision_vs_gt,
                "novel_count": len(novel_context),
                "false_neg_count": len(false_negatives),
                "creation_bucket": meta.get("creation_bucket", "unknown"),
                "new_file_count": meta.get("new_file_count", 0),
                "total_files": meta.get("total_files", 0),
            },
        )

    return score


def _compute_structural_signals(
    novel_defs: list[dict],
    gt_defs: list[dict],
    meta: dict,
) -> list[dict[str, Any]]:
    """Compute structural relationship signals for each novel def.

    Signals computed:
    - shares_file_with_gt: Any GT def in the same file
    - same_directory_as_gt: In a directory containing GT defs
    - is_in_new_file: The def lives in a newly created file
    - is_in_modified_file: The def lives in a modified file
    - access_type: How the agent found it (recon, impact, file_read, grep)
    - turn: Which turn it was discovered
    """
    gt_paths = {d.get("path", "") for d in gt_defs}
    gt_dirs = {str(Path(p).parent) for p in gt_paths if p}
    new_paths = set(meta.get("new_file_paths", []))
    mod_paths = set(meta.get("modified_file_paths", []))

    from pathlib import Path as _Path  # noqa: E811

    signals = []
    for nd in novel_defs:
        path = nd.get("path", "")
        parent_dir = str(_Path(path).parent) if path else ""
        signals.append({
            "def_uid": nd.get("def_uid"),
            "def_key": nd.get("def_key"),
            "shares_file_with_gt": path in gt_paths,
            "same_directory_as_gt": parent_dir in gt_dirs,
            "is_in_new_file": path in new_paths,
            "is_in_modified_file": path in mod_paths,
            "is_in_unchanged_file": path not in new_paths and path not in mod_paths,
            "access_type": nd.get("access_type", ""),
            "turn": nd.get("turn", 0),
            "kind": nd.get("kind", ""),
        })
    return signals
