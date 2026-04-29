"""Tests for index/_internal/db/consistency.py — derived signal consistency."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from coderecon.index.db.consistency import (
    ConsistencyReport,
    SignalCheck,
    SignalGap,
    backfill_gaps,
    check_consistency,
)

# ── Data class tests ──────────────────────────────────────────────

class TestSignalGap:
    def test_basic_construction(self) -> None:
        gap = SignalGap(signal="splade_vecs", reason="missing", file_ids=[1, 2], gap_count=5)
        assert gap.signal == "splade_vecs"
        assert gap.reason == "missing"
        assert gap.file_ids == [1, 2]
        assert gap.gap_count == 5

class TestConsistencyReport:
    def test_empty_report_is_consistent(self) -> None:
        report = ConsistencyReport()
        assert report.consistent is True
        assert report.total_gaps == 0

    def test_report_with_gaps(self) -> None:
        report = ConsistencyReport(
            gaps=[
                SignalGap(signal="a", reason="missing", file_ids=[1], gap_count=3),
                SignalGap(signal="b", reason="stale", file_ids=[2], gap_count=7),
            ]
        )
        assert report.consistent is False
        assert report.total_gaps == 10

# ── check_consistency tests ───────────────────────────────────────

class TestCheckConsistency:
    def test_all_consistent_returns_empty(self) -> None:
        mock_db = MagicMock()
        mock_check = SignalCheck(name="test_signal", run=lambda db: [])
        with patch(
            "coderecon.index.db.consistency._get_signal_checks",
            return_value=[mock_check],
        ):
            report = check_consistency(mock_db)
        assert report.consistent is True
        mock_db.create_all.assert_called_once()

    def test_gap_detected(self) -> None:
        mock_db = MagicMock()
        gap = SignalGap(signal="splade_vecs", reason="missing", file_ids=[1], gap_count=1)
        mock_check = SignalCheck(name="splade_vecs", run=lambda db: [gap])
        with patch(
            "coderecon.index.db.consistency._get_signal_checks",
            return_value=[mock_check],
        ):
            report = check_consistency(mock_db)
        assert not report.consistent
        assert len(report.gaps) == 1
        assert report.gaps[0].signal == "splade_vecs"

    def test_check_failure_is_graceful(self) -> None:
        mock_db = MagicMock()

        def _failing_check(db: object) -> list[SignalGap]:
            raise RuntimeError("DB unavailable")

        mock_check = SignalCheck(name="broken", run=_failing_check)
        with patch(
            "coderecon.index.db.consistency._get_signal_checks",
            return_value=[mock_check],
        ):
            report = check_consistency(mock_db)
        # Should not raise — just skip the broken check
        assert report.consistent is True

    def test_multiple_checks_aggregate(self) -> None:
        mock_db = MagicMock()
        gap_a = SignalGap(signal="a", reason="missing", file_ids=[1], gap_count=2)
        gap_b = SignalGap(signal="b", reason="stale", file_ids=[3], gap_count=4)
        checks = [
            SignalCheck(name="a", run=lambda db: [gap_a]),
            SignalCheck(name="b", run=lambda db: [gap_b]),
        ]
        with patch(
            "coderecon.index.db.consistency._get_signal_checks",
            return_value=checks,
        ):
            report = check_consistency(mock_db)
        assert report.total_gaps == 6
        assert len(report.gaps) == 2

# ── backfill_gaps tests ───────────────────────────────────────────

class TestBackfillGaps:
    def test_consistent_report_skips(self) -> None:
        mock_db = MagicMock()
        report = ConsistencyReport()
        result = backfill_gaps(mock_db, report)
        assert result == {}

    def test_backfill_runs_for_gaps(self) -> None:
        mock_db = MagicMock()
        gap = SignalGap(signal="splade_vecs", reason="missing", file_ids=[1, 2], gap_count=5)
        report = ConsistencyReport(gaps=[gap])
        mock_backfill = MagicMock(return_value=5)
        check = SignalCheck(name="splade_vecs", run=lambda db: [], backfill=mock_backfill)

        with patch(
            "coderecon.index.db.consistency._get_signal_checks",
            return_value=[check],
        ):
            result = backfill_gaps(mock_db, report)

        assert result == {"splade_vecs": 5}
        mock_backfill.assert_called_once_with(mock_db, [1, 2])

    def test_backfill_skips_unknown_signal(self) -> None:
        mock_db = MagicMock()
        gap = SignalGap(signal="unknown", reason="missing", file_ids=[], gap_count=1)
        report = ConsistencyReport(gaps=[gap])

        with patch(
            "coderecon.index.db.consistency._get_signal_checks",
            return_value=[],
        ):
            result = backfill_gaps(mock_db, report)
        assert result == {}

    def test_backfill_handles_failure(self) -> None:
        mock_db = MagicMock()
        gap = SignalGap(signal="splade_vecs", reason="missing", file_ids=[1], gap_count=1)
        report = ConsistencyReport(gaps=[gap])

        def _failing_backfill(db: object, file_ids: list[int]) -> int:
            raise RuntimeError("Backfill failed")

        check = SignalCheck(name="splade_vecs", run=lambda db: [], backfill=_failing_backfill)

        with patch(
            "coderecon.index.db.consistency._get_signal_checks",
            return_value=[check],
        ):
            result = backfill_gaps(mock_db, report)
        # Should not raise
        assert result == {}

    def test_backfill_skips_no_backfill_fn(self) -> None:
        mock_db = MagicMock()
        gap = SignalGap(signal="test", reason="missing", file_ids=[], gap_count=1)
        report = ConsistencyReport(gaps=[gap])
        check = SignalCheck(name="test", run=lambda db: [], backfill=None)

        with patch(
            "coderecon.index.db.consistency._get_signal_checks",
            return_value=[check],
        ):
            result = backfill_gaps(mock_db, report)
        assert result == {}
