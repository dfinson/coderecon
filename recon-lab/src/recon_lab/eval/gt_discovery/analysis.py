"""Post-hoc analysis and rule mining from GT discovery experiment results.

Reads Inspect AI eval logs and mines structural signal conjunctions
that predict whether a novel-context def should be included in GT.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Rule:
    """A conjunction of structural signals forming a GT expansion rule."""

    conditions: tuple[str, ...]
    precision: float
    support: int  # how many novel defs matched
    examples: int  # how many of those were later confirmed relevant


@dataclass
class AnalysisResult:
    """Result of rule mining across an eval run."""

    total_samples: int = 0
    total_novel_defs: int = 0
    total_true_pos: int = 0
    total_false_neg: int = 0
    rules: list[Rule] = field(default_factory=list)
    bucket_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    signal_distribution: dict[str, int] = field(default_factory=dict)


def analyze_eval_log(log_path: str | Path) -> AnalysisResult:
    """Analyze a single eval log file from Inspect AI.

    Args:
        log_path: Path to the .eval JSON log file.

    Returns:
        AnalysisResult with mined rules and statistics.
    """
    log_path = Path(log_path).expanduser()
    log_data = json.loads(log_path.read_text())

    samples = log_data.get("samples", [])
    result = AnalysisResult(total_samples=len(samples))

    all_novel_signals: list[dict] = []
    bucket_accum: dict[str, dict[str, list]] = defaultdict(
        lambda: {"recall": [], "precision": [], "novel_count": [], "false_neg": []}
    )

    for sample in samples:
        score_meta = sample.get("scores", {}).get("gt_discovery_scorer", {}).get("metadata", {})
        if not score_meta:
            continue

        bucket = score_meta.get("creation_bucket", "unknown")
        bucket_accum[bucket]["recall"].append(score_meta.get("recall", 0))
        bucket_accum[bucket]["precision"].append(score_meta.get("precision_vs_gt", 0))
        bucket_accum[bucket]["novel_count"].append(score_meta.get("novel_count", 0))
        bucket_accum[bucket]["false_neg"].append(score_meta.get("false_neg_count", 0))

        result.total_true_pos += len(score_meta.get("true_positives", []))
        result.total_false_neg += len(score_meta.get("false_negatives", []))

        signals = score_meta.get("structural_signals", [])
        result.total_novel_defs += len(signals)
        all_novel_signals.extend(signals)

    # Bucket statistics
    for bucket, vals in bucket_accum.items():
        n = len(vals["recall"])
        result.bucket_stats[bucket] = {
            "count": n,
            "mean_recall": sum(vals["recall"]) / n if n else 0,
            "mean_precision": sum(vals["precision"]) / n if n else 0,
            "mean_novel_count": sum(vals["novel_count"]) / n if n else 0,
            "mean_false_neg": sum(vals["false_neg"]) / n if n else 0,
        }

    # Signal distribution
    signal_names = [
        "shares_file_with_gt",
        "same_directory_as_gt",
        "is_in_new_file",
        "is_in_modified_file",
        "is_in_unchanged_file",
    ]
    for sig in signal_names:
        result.signal_distribution[sig] = sum(
            1 for s in all_novel_signals if s.get(sig)
        )

    # Rule mining: find conjunctions with high "relevance" signal
    # A novel def is "relevant" if it shares structural properties
    # with GT (proxy for what a human would confirm as GT-worthy).
    # Using shares_file_with_gt OR same_directory_as_gt as proxy
    # relevance until we have human annotations.
    result.rules = _mine_rules(all_novel_signals, signal_names)

    return result


def _mine_rules(
    signals: list[dict],
    signal_names: list[str],
    min_precision: float = 0.8,
    min_support: int = 5,
) -> list[Rule]:
    """Mine conjunctive rules from structural signals.

    A def is considered "likely relevant" if it shares a file with GT
    OR is in a new/modified file in the same directory as GT.
    This is a bootstrap heuristic — the real validation comes from
    human review of the top rules.
    """

    def _is_likely_relevant(s: dict) -> bool:
        """Bootstrap relevance proxy — conservative."""
        if s.get("shares_file_with_gt"):
            return True
        if s.get("same_directory_as_gt") and s.get("is_in_new_file"):
            return True
        if s.get("same_directory_as_gt") and s.get("is_in_modified_file"):
            return True
        return False

    if not signals:
        return []

    rules: list[Rule] = []

    # Generate all 1- and 2-signal conjunctions
    for r in range(1, 3):
        for combo in combinations(signal_names, r):
            matching = [s for s in signals if all(s.get(c) for c in combo)]
            if len(matching) < min_support:
                continue
            relevant = sum(1 for s in matching if _is_likely_relevant(s))
            precision = relevant / len(matching)
            if precision >= min_precision:
                rules.append(Rule(
                    conditions=combo,
                    precision=precision,
                    support=len(matching),
                    examples=relevant,
                ))

    # Sort by support (higher is better among rules meeting precision threshold)
    rules.sort(key=lambda r: (-r.precision, -r.support))
    return rules


def print_analysis(result: AnalysisResult) -> str:
    """Format analysis result as a readable report."""
    lines = [
        "=" * 70,
        "GT DISCOVERY EXPERIMENT — ANALYSIS REPORT",
        "=" * 70,
        "",
        f"Total samples: {result.total_samples}",
        f"Total novel defs found: {result.total_novel_defs}",
        f"Total GT true positives: {result.total_true_pos}",
        f"Total GT false negatives: {result.total_false_neg}",
        "",
        "─── Bucket Statistics ───",
    ]

    for bucket, stats in sorted(result.bucket_stats.items()):
        lines.append(f"\n  [{bucket}] (n={stats['count']})")
        lines.append(f"    Mean recall:       {stats['mean_recall']:.2%}")
        lines.append(f"    Mean precision:    {stats['mean_precision']:.2%}")
        lines.append(f"    Mean novel count:  {stats['mean_novel_count']:.1f}")
        lines.append(f"    Mean false neg:    {stats['mean_false_neg']:.1f}")

    lines.append("\n─── Signal Distribution ───")
    for sig, count in sorted(result.signal_distribution.items(), key=lambda x: -x[1]):
        pct = count / result.total_novel_defs if result.total_novel_defs else 0
        lines.append(f"  {sig:<30} {count:>5} ({pct:.1%})")

    lines.append("\n─── Mined Rules (precision ≥ 80%) ───")
    if not result.rules:
        lines.append("  (no rules met threshold)")
    for i, rule in enumerate(result.rules[:20], 1):
        conds = " AND ".join(rule.conditions)
        lines.append(
            f"  {i:>2}. [{conds}]  "
            f"precision={rule.precision:.2%}  support={rule.support}  "
            f"examples={rule.examples}"
        )

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)
