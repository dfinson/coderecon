"""Drift / health trend analysis — track coverage & lint health across epochs.

Records snapshots of key health metrics per epoch and computes trends.
Used by recon_understand and governance policies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """Point-in-time health metrics."""

    epoch: int
    coverage_rate: float  # 0.0-1.0
    covered_defs: int
    total_defs: int
    lint_error_count: int
    lint_warning_count: int
    clean_file_count: int
    total_files: int
    cycle_count: int


@dataclass(slots=True)
class HealthTrend:
    """Health trend over recent epochs."""

    snapshots: list[HealthSnapshot] = field(default_factory=list)

    @property
    def latest(self) -> HealthSnapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    @property
    def coverage_direction(self) -> str:
        """'improving', 'declining', 'stable', or 'unknown'."""
        if len(self.snapshots) < 2:  # noqa: PLR2004
            return "unknown"
        recent = self.snapshots[-1].coverage_rate
        previous = self.snapshots[-2].coverage_rate
        if recent > previous + 0.01:
            return "improving"
        if recent < previous - 0.01:
            return "declining"
        return "stable"

    @property
    def lint_direction(self) -> str:
        if len(self.snapshots) < 2:  # noqa: PLR2004
            return "unknown"
        recent = self.snapshots[-1].lint_error_count
        previous = self.snapshots[-2].lint_error_count
        if recent < previous:
            return "improving"
        if recent > previous:
            return "declining"
        return "stable"

    def to_dict(self) -> dict[str, object]:
        latest = self.latest
        return {
            "coverage_direction": self.coverage_direction,
            "lint_direction": self.lint_direction,
            "latest": {
                "epoch": latest.epoch,
                "coverage_rate": round(latest.coverage_rate, 4),
                "covered_defs": latest.covered_defs,
                "total_defs": latest.total_defs,
                "lint_errors": latest.lint_error_count,
                "lint_warnings": latest.lint_warning_count,
                "clean_files": latest.clean_file_count,
                "total_files": latest.total_files,
                "cycles": latest.cycle_count,
            }
            if latest
            else None,
            "history_length": len(self.snapshots),
        }


def capture_snapshot(engine: Engine, epoch: int) -> HealthSnapshot:
    """Capture current health metrics as a snapshot.

    Reads from LintStatusFact, TestCoverageFact, and graph analysis.
    """
    with engine.connect() as conn:
        # Coverage stats
        cov_row = conn.execute(
            text(
                "SELECT "
                "  COUNT(DISTINCT target_def_uid) AS covered, "
                "  (SELECT COUNT(*) FROM def_facts WHERE kind NOT IN ('variable', 'constant')) AS total "
                "FROM test_coverage_facts WHERE stale = 0"
            )
        ).fetchone()

        covered_defs = cov_row[0] if cov_row else 0
        total_defs = cov_row[1] if cov_row else 0
        coverage_rate = covered_defs / total_defs if total_defs > 0 else 0.0

        # Lint stats
        lint_row = conn.execute(
            text(
                "SELECT "
                "  COALESCE(SUM(error_count), 0), "
                "  COALESCE(SUM(warning_count), 0), "
                "  COALESCE(SUM(CASE WHEN clean = 1 THEN 1 ELSE 0 END), 0), "
                "  COUNT(DISTINCT file_path) "
                "FROM lint_status_facts"
            )
        ).fetchone()

        lint_errors = lint_row[0] if lint_row else 0
        lint_warnings = lint_row[1] if lint_row else 0
        clean_files = lint_row[2] if lint_row else 0
        total_files = lint_row[3] if lint_row else 0

        # Cycle count (lightweight: just count SCC > 1)
        cycle_count = 0
        try:
            from coderecon.index._internal.analysis.code_graph import (
                build_file_graph,
                detect_cycles,
            )

            fg = build_file_graph(engine)
            cycles = detect_cycles(fg)
            cycle_count = len(cycles)
        except Exception:
            pass

    return HealthSnapshot(
        epoch=epoch,
        coverage_rate=coverage_rate,
        covered_defs=covered_defs,
        total_defs=total_defs,
        lint_error_count=lint_errors,
        lint_warning_count=lint_warnings,
        clean_file_count=clean_files,
        total_files=total_files,
        cycle_count=cycle_count,
    )


# ── Snapshot persistence ──
# We store snapshots in a simple JSON-lines file in .recon/ since they're
# lightweight and don't need SQL queries.

_SNAPSHOT_FILE = "health_snapshots.jsonl"


def persist_snapshot(recon_dir, snapshot: HealthSnapshot) -> None:  # noqa: ANN001
    """Append a snapshot to the JSONL file."""
    import json
    from pathlib import Path

    path = Path(recon_dir) / _SNAPSHOT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "epoch": snapshot.epoch,
        "coverage_rate": snapshot.coverage_rate,
        "covered_defs": snapshot.covered_defs,
        "total_defs": snapshot.total_defs,
        "lint_errors": snapshot.lint_error_count,
        "lint_warnings": snapshot.lint_warning_count,
        "clean_files": snapshot.clean_file_count,
        "total_files": snapshot.total_files,
        "cycles": snapshot.cycle_count,
    }

    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def load_trend(recon_dir, max_snapshots: int = 20) -> HealthTrend:  # noqa: ANN001
    """Load recent health snapshots and compute trend."""
    import json
    from pathlib import Path

    path = Path(recon_dir) / _SNAPSHOT_FILE
    if not path.exists():
        return HealthTrend()

    snapshots = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            snapshots.append(HealthSnapshot(
                epoch=d["epoch"],
                coverage_rate=d["coverage_rate"],
                covered_defs=d["covered_defs"],
                total_defs=d["total_defs"],
                lint_error_count=d["lint_errors"],
                lint_warning_count=d["lint_warnings"],
                clean_file_count=d["clean_files"],
                total_files=d["total_files"],
                cycle_count=d["cycles"],
            ))
        except (json.JSONDecodeError, KeyError):
            continue

    # Keep only the most recent
    return HealthTrend(snapshots=snapshots[-max_snapshots:])
