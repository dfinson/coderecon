"""Gate evaluation engine — governance policy enforcement at checkpoint.

Evaluates governance rules from config against current state.
Returns pass/fail verdicts that checkpoint can use to block or warn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from coderecon.config.models import GovernanceConfig

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GateViolation:
    """A governance rule violation."""

    rule: str
    level: str  # "error", "warning", "info"
    message: str
    details: dict[str, object] | None = None


@dataclass(slots=True)
class GateResult:
    """Aggregated gate evaluation result."""

    violations: list[GateViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True if no error-level violations."""
        return not any(v.level == "error" for v in self.violations)

    @property
    def errors(self) -> list[GateViolation]:
        return [v for v in self.violations if v.level == "error"]

    @property
    def warnings(self) -> list[GateViolation]:
        return [v for v in self.violations if v.level == "warning"]

    @property
    def infos(self) -> list[GateViolation]:
        return [v for v in self.violations if v.level == "info"]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "info_count": len(self.infos),
            "violations": [
                {
                    "rule": v.rule,
                    "level": v.level,
                    "message": v.message,
                    **({"details": v.details} if v.details else {}),
                }
                for v in self.violations
            ],
        }


def evaluate_gates(
    governance: GovernanceConfig,
    engine: Engine,
    *,
    changed_files: list[str] | None = None,
    changed_def_uids: list[str] | None = None,
    test_debt_info: dict[str, object] | None = None,
    lint_clean: bool | None = None,
    lint_diagnostics: int = 0,
) -> GateResult:
    """Evaluate all enabled governance rules.

    Args:
        governance: GovernanceConfig from the repo config.
        engine: SQLAlchemy engine for fact reads.
        changed_files: Files involved in this checkpoint.
        changed_def_uids: DefFact UIDs that changed.
        test_debt_info: Output from _detect_test_debt if available.
        lint_clean: Whether lint passed (True) or failed (False).
        lint_diagnostics: Number of lint diagnostics found.

    Returns:
        GateResult with all violations.
    """
    result = GateResult()

    # --- Coverage floor ---
    rule = governance.coverage_floor
    if rule.enabled:
        _check_coverage_floor(engine, rule, result)

    # --- Lint clean ---
    rule = governance.lint_clean
    if rule.enabled:
        _check_lint_clean(rule, result, lint_clean, lint_diagnostics)

    # --- No new cycles ---
    rule = governance.no_new_cycles
    if rule.enabled:
        _check_no_new_cycles(engine, rule, result, changed_files)

    # --- Test debt ---
    rule = governance.test_debt
    if rule.enabled and test_debt_info:
        _check_test_debt(rule, result, test_debt_info)

    # --- Coverage regression ---
    rule = governance.coverage_regression
    if rule.enabled and changed_def_uids:
        _check_coverage_regression(engine, rule, result, changed_def_uids)

    # --- Centrality impact ---
    rule = governance.centrality_impact
    if rule.enabled and changed_def_uids:
        _check_centrality_impact(engine, rule, result, changed_def_uids)

    return result


def _check_coverage_floor(
    engine: Engine,
    rule: object,
    result: GateResult,
) -> None:
    """Check that overall coverage meets the floor threshold."""
    try:
        row = engine.connect().execute(
            text(
                "SELECT "
                "  COUNT(DISTINCT target_def_uid) AS covered, "
                "  (SELECT COUNT(*) FROM def_facts WHERE kind NOT IN ('variable', 'constant')) AS total "
                "FROM test_coverage_facts WHERE stale = 0"
            )
        ).fetchone()

        if row and row[1] > 0:
            pct = (row[0] / row[1]) * 100
            threshold = rule.threshold or 80.0
            if pct < threshold:
                msg = rule.message.replace("{threshold}", str(threshold))
                result.violations.append(GateViolation(
                    rule="coverage_floor",
                    level=rule.level,
                    message=f"{msg} Current: {pct:.1f}%",
                    details={"current": pct, "threshold": threshold},
                ))
    except Exception:
        logger.debug("gate.coverage_floor.failed", exc_info=True)


def _check_lint_clean(
    rule: object,
    result: GateResult,
    lint_clean: bool | None,
    lint_diagnostics: int,
) -> None:
    """Check that lint is clean."""
    if lint_clean is False or lint_diagnostics > 0:
        result.violations.append(GateViolation(
            rule="lint_clean",
            level=rule.level,
            message=rule.message or "Lint errors must be resolved.",
            details={"diagnostics": lint_diagnostics},
        ))


def _check_no_new_cycles(
    engine: Engine,
    rule: object,
    result: GateResult,
    changed_files: list[str] | None,
) -> None:
    """Check that changed files don't introduce new cycles."""
    if not changed_files:
        return

    try:
        from coderecon.index._internal.analysis.code_graph import (
            build_file_graph,
            detect_cycles,
        )

        g = build_file_graph(engine)
        cycles = detect_cycles(g)

        # Check if any changed file is in a cycle
        changed_set = set(changed_files)
        new_cycles = [
            c for c in cycles
            if c.nodes & frozenset(changed_set)
        ]

        if new_cycles:
            result.violations.append(GateViolation(
                rule="no_new_cycles",
                level=rule.level,
                message=rule.message or "New circular dependencies detected.",
                details={
                    "cycle_count": len(new_cycles),
                    "involved_files": sorted(
                        {n for c in new_cycles for n in c.nodes if n in changed_set}
                    ),
                },
            ))
    except Exception:
        logger.debug("gate.no_new_cycles.failed", exc_info=True)


def _check_test_debt(
    rule: object,
    result: GateResult,
    test_debt_info: dict[str, object],
) -> None:
    """Check for test debt (source changed without test updates)."""
    missing = test_debt_info.get("missing_test_updates", [])
    if missing:
        result.violations.append(GateViolation(
            rule="test_debt",
            level=rule.level,
            message=rule.message or "Source files changed without test updates.",
            details={"missing_updates": len(missing), "files": missing[:5]},
        ))


def _check_coverage_regression(
    engine: Engine,
    rule: object,
    result: GateResult,
    changed_def_uids: list[str],
) -> None:
    """Check for coverage regression on changed defs."""
    try:
        placeholders = ", ".join(f":p{i}" for i in range(len(changed_def_uids)))
        params: dict[str, str | float] = {
            f"p{i}": uid for i, uid in enumerate(changed_def_uids)
        }

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT target_def_uid, line_rate FROM test_coverage_facts "
                    f"WHERE target_def_uid IN ({placeholders}) AND stale = 0"
                ),
                params,
            ).fetchall()

        if rows:
            avg_rate = sum(r[1] for r in rows) / len(rows)
            threshold = rule.threshold or 0.0
            # If average rate dropped below threshold, flag it
            if avg_rate * 100 < threshold:
                msg = (rule.message or "Coverage regression.").replace(
                    "{threshold}", str(threshold)
                )
                result.violations.append(GateViolation(
                    rule="coverage_regression",
                    level=rule.level,
                    message=f"{msg} Current: {avg_rate * 100:.1f}%",
                    details={"current_rate": avg_rate, "threshold": threshold},
                ))
    except Exception:
        logger.debug("gate.coverage_regression.failed", exc_info=True)


def _check_centrality_impact(
    engine: Engine,
    rule: object,
    result: GateResult,
    changed_def_uids: list[str],
) -> None:
    """Check if changed defs are high-centrality symbols."""
    try:
        from coderecon.index._internal.analysis.code_graph import (
            build_def_graph,
            compute_pagerank,
        )

        g = build_def_graph(engine)
        if g.number_of_nodes() == 0:
            return

        top_defs = compute_pagerank(g, top_k=max(1, int(g.number_of_nodes() * 0.1)))
        top_uids = {s.def_uid for s in top_defs}
        threshold = rule.threshold or 0.8

        impacted = [uid for uid in changed_def_uids if uid in top_uids]
        if impacted:
            result.violations.append(GateViolation(
                rule="centrality_impact",
                level=rule.level,
                message=(rule.message or "High-centrality symbol changed.").replace(
                    "{threshold}", str(threshold)
                ),
                details={"impacted_symbols": impacted[:5], "count": len(impacted)},
            ))
    except Exception:
        logger.debug("gate.centrality_impact.failed", exc_info=True)
