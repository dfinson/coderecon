"""Tests for health trend analysis."""

from __future__ import annotations

from pathlib import Path

from coderecon.index._internal.analysis.health_trend import (
    HealthSnapshot,
    HealthTrend,
    load_trend,
    persist_snapshot,
)


class TestHealthSnapshot:
    def test_snapshot_fields(self) -> None:
        s = HealthSnapshot(
            epoch=1,
            coverage_rate=0.85,
            covered_defs=17,
            total_defs=20,
            lint_error_count=2,
            lint_warning_count=5,
            clean_file_count=8,
            total_files=10,
            cycle_count=1,
        )
        assert s.coverage_rate == 0.85
        assert s.cycle_count == 1


class TestHealthTrend:
    def test_empty_trend(self) -> None:
        t = HealthTrend()
        assert t.latest is None
        assert t.coverage_direction == "unknown"
        assert t.lint_direction == "unknown"

    def test_single_snapshot(self) -> None:
        s = HealthSnapshot(1, 0.8, 16, 20, 0, 0, 10, 10, 0)
        t = HealthTrend(snapshots=[s])
        assert t.latest == s
        assert t.coverage_direction == "unknown"

    def test_improving_coverage(self) -> None:
        s1 = HealthSnapshot(1, 0.7, 14, 20, 0, 0, 10, 10, 0)
        s2 = HealthSnapshot(2, 0.85, 17, 20, 0, 0, 10, 10, 0)
        t = HealthTrend(snapshots=[s1, s2])
        assert t.coverage_direction == "improving"

    def test_declining_coverage(self) -> None:
        s1 = HealthSnapshot(1, 0.9, 18, 20, 0, 0, 10, 10, 0)
        s2 = HealthSnapshot(2, 0.7, 14, 20, 0, 0, 10, 10, 0)
        t = HealthTrend(snapshots=[s1, s2])
        assert t.coverage_direction == "declining"

    def test_stable_coverage(self) -> None:
        s1 = HealthSnapshot(1, 0.8, 16, 20, 0, 0, 10, 10, 0)
        s2 = HealthSnapshot(2, 0.805, 16, 20, 0, 0, 10, 10, 0)
        t = HealthTrend(snapshots=[s1, s2])
        assert t.coverage_direction == "stable"

    def test_improving_lint(self) -> None:
        s1 = HealthSnapshot(1, 0.8, 16, 20, 5, 0, 5, 10, 0)
        s2 = HealthSnapshot(2, 0.8, 16, 20, 2, 0, 8, 10, 0)
        t = HealthTrend(snapshots=[s1, s2])
        assert t.lint_direction == "improving"

    def test_to_dict(self) -> None:
        s = HealthSnapshot(1, 0.8, 16, 20, 0, 3, 7, 10, 1)
        t = HealthTrend(snapshots=[s])
        d = t.to_dict()
        assert d["history_length"] == 1
        assert d["latest"]["coverage_rate"] == 0.8
        assert d["latest"]["cycles"] == 1


class TestPersistence:
    def test_round_trip(self, tmp_path: Path) -> None:
        s = HealthSnapshot(1, 0.8, 16, 20, 0, 0, 10, 10, 0)
        persist_snapshot(tmp_path, s)

        t = load_trend(tmp_path)
        assert len(t.snapshots) == 1
        assert t.snapshots[0].epoch == 1
        assert t.snapshots[0].coverage_rate == 0.8

    def test_multiple_snapshots(self, tmp_path: Path) -> None:
        for i in range(5):
            s = HealthSnapshot(i, 0.5 + i * 0.1, i * 4, 20, 5 - i, 0, 5 + i, 10, 0)
            persist_snapshot(tmp_path, s)

        t = load_trend(tmp_path)
        assert len(t.snapshots) == 5
        assert t.coverage_direction == "improving"

    def test_max_snapshots(self, tmp_path: Path) -> None:
        for i in range(30):
            s = HealthSnapshot(i, 0.5, 10, 20, 0, 0, 10, 10, 0)
            persist_snapshot(tmp_path, s)

        t = load_trend(tmp_path, max_snapshots=10)
        assert len(t.snapshots) == 10

    def test_missing_file(self, tmp_path: Path) -> None:
        t = load_trend(tmp_path)
        assert len(t.snapshots) == 0
