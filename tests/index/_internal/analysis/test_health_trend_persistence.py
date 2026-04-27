"""Tests for index/_internal/analysis/health_trend.py — health snapshots and trends."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from coderecon.index._internal.analysis.health_trend import (
    HealthSnapshot,
    HealthTrend,
    load_trend,
    persist_snapshot,
)

def _snap(
    epoch: int = 1,
    coverage_rate: float = 0.5,
    covered_defs: int = 50,
    total_defs: int = 100,
    lint_errors: int = 0,
    lint_warnings: int = 0,
    clean_files: int = 10,
    total_files: int = 10,
    cycles: int = 0,
) -> HealthSnapshot:
    return HealthSnapshot(
        epoch=epoch,
        coverage_rate=coverage_rate,
        covered_defs=covered_defs,
        total_defs=total_defs,
        lint_error_count=lint_errors,
        lint_warning_count=lint_warnings,
        clean_file_count=clean_files,
        total_files=total_files,
        cycle_count=cycles,
    )

# ── HealthSnapshot tests ─────────────────────────────────────────

class TestHealthSnapshot:
    def test_construction(self) -> None:
        s = _snap(epoch=5, coverage_rate=0.8)
        assert s.epoch == 5
        assert s.coverage_rate == 0.8

    def test_frozen(self) -> None:
        s = _snap()
        with pytest.raises(AttributeError):
            s.epoch = 99  # type: ignore[misc]

# ── HealthTrend tests ─────────────────────────────────────────────

class TestHealthTrend:
    def test_empty_trend(self) -> None:
        t = HealthTrend()
        assert t.latest is None
        assert t.coverage_direction == "unknown"
        assert t.lint_direction == "unknown"

    def test_single_snapshot(self) -> None:
        t = HealthTrend(snapshots=[_snap(epoch=1)])
        assert t.latest is not None
        assert t.latest.epoch == 1
        assert t.coverage_direction == "unknown"

    def test_coverage_improving(self) -> None:
        t = HealthTrend(snapshots=[
            _snap(epoch=1, coverage_rate=0.50),
            _snap(epoch=2, coverage_rate=0.65),
        ])
        assert t.coverage_direction == "improving"

    def test_coverage_declining(self) -> None:
        t = HealthTrend(snapshots=[
            _snap(epoch=1, coverage_rate=0.65),
            _snap(epoch=2, coverage_rate=0.50),
        ])
        assert t.coverage_direction == "declining"

    def test_coverage_stable(self) -> None:
        t = HealthTrend(snapshots=[
            _snap(epoch=1, coverage_rate=0.50),
            _snap(epoch=2, coverage_rate=0.505),
        ])
        assert t.coverage_direction == "stable"

    def test_lint_improving(self) -> None:
        t = HealthTrend(snapshots=[
            _snap(epoch=1, lint_errors=10),
            _snap(epoch=2, lint_errors=5),
        ])
        assert t.lint_direction == "improving"

    def test_lint_declining(self) -> None:
        t = HealthTrend(snapshots=[
            _snap(epoch=1, lint_errors=5),
            _snap(epoch=2, lint_errors=10),
        ])
        assert t.lint_direction == "declining"

    def test_lint_stable(self) -> None:
        t = HealthTrend(snapshots=[
            _snap(epoch=1, lint_errors=5),
            _snap(epoch=2, lint_errors=5),
        ])
        assert t.lint_direction == "stable"

    def test_to_dict_with_latest(self) -> None:
        t = HealthTrend(snapshots=[_snap(epoch=3, coverage_rate=0.75, lint_errors=2)])
        d = t.to_dict()
        assert d["coverage_direction"] == "unknown"
        assert d["history_length"] == 1
        assert d["latest"]["epoch"] == 3
        assert d["latest"]["coverage_rate"] == 0.75
        assert d["latest"]["lint_errors"] == 2

    def test_to_dict_empty(self) -> None:
        d = HealthTrend().to_dict()
        assert d["latest"] is None
        assert d["history_length"] == 0

# ── Persistence tests ─────────────────────────────────────────────

class TestPersistence:
    def test_persist_and_load(self) -> None:
        with TemporaryDirectory() as tmpdir:
            snap = _snap(epoch=1, coverage_rate=0.6, lint_errors=3, cycles=1)
            persist_snapshot(tmpdir, snap)
            trend = load_trend(tmpdir)
        assert len(trend.snapshots) == 1
        loaded = trend.snapshots[0]
        assert loaded.epoch == 1
        assert loaded.coverage_rate == 0.6
        assert loaded.lint_error_count == 3
        assert loaded.cycle_count == 1

    def test_multiple_snapshots(self) -> None:
        with TemporaryDirectory() as tmpdir:
            persist_snapshot(tmpdir, _snap(epoch=1, coverage_rate=0.5))
            persist_snapshot(tmpdir, _snap(epoch=2, coverage_rate=0.7))
            trend = load_trend(tmpdir)
        assert len(trend.snapshots) == 2
        assert trend.coverage_direction == "improving"

    def test_load_empty_dir(self) -> None:
        with TemporaryDirectory() as tmpdir:
            trend = load_trend(tmpdir)
        assert len(trend.snapshots) == 0
        assert trend.latest is None

    def test_load_max_snapshots(self) -> None:
        with TemporaryDirectory() as tmpdir:
            for i in range(30):
                persist_snapshot(tmpdir, _snap(epoch=i))
            trend = load_trend(tmpdir, max_snapshots=5)
        assert len(trend.snapshots) == 5
        assert trend.snapshots[0].epoch == 25

    def test_load_ignores_corrupted_lines(self) -> None:
        with TemporaryDirectory() as tmpdir:
            persist_snapshot(tmpdir, _snap(epoch=1))
            # Append a corrupt line
            path = Path(tmpdir) / "health_snapshots.jsonl"
            with path.open("a") as f:
                f.write("not json\n")
            persist_snapshot(tmpdir, _snap(epoch=2))
            trend = load_trend(tmpdir)
        assert len(trend.snapshots) == 2
