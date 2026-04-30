"""Tests for index.ops_coverage — post-reindex coverage collection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@dataclass
class _FakeGraphResult:
    test_files: list[str]

    def affected_tests(self, changed_files: list[str]) -> "_FakeGraphResult":
        return self


@dataclass
class _FakeFailure:
    path: str
    name: str


def _make_engine(*, affected_tests: list[str] | None = None):  # noqa: ANN202
    engine = MagicMock()
    engine.repo_root = Path("/repo")
    engine.current_epoch = 5
    engine.db.engine = MagicMock()
    # Mock db.session() context manager for ImportGraph usage
    mock_session = MagicMock()
    engine.db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
    engine.db.session.return_value.__exit__ = MagicMock(return_value=False)
    engine._affected_tests = affected_tests or []
    engine.wait_for_freshness = AsyncMock()
    return engine


class TestRunCoverageForChangedFiles:
    """Incremental coverage after reindex."""

    @pytest.mark.asyncio
    async def test_returns_zero_for_empty_files(self) -> None:
        from coderecon.index.ops_coverage import run_coverage_for_changed_files

        engine = _make_engine()
        result = await run_coverage_for_changed_files(engine, [])
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_affected_tests(self) -> None:
        from coderecon.index.ops_coverage import run_coverage_for_changed_files

        engine = _make_engine(affected_tests=[])
        with patch("coderecon.index.graph.import_graph.ImportGraph") as MockGraph:
            MockGraph.return_value.affected_tests.return_value = _FakeGraphResult(test_files=[])
            result = await run_coverage_for_changed_files(engine, ["src/a.py"])
        assert result == 0

    @pytest.mark.asyncio
    async def test_runs_affected_tests_with_coverage(self) -> None:
        from coderecon.index.ops_coverage import run_coverage_for_changed_files

        engine = _make_engine(affected_tests=["tests/test_a.py"])

        mock_test_result = MagicMock()
        mock_test_result.run_status = None

        with (
            patch("coderecon.index.graph.import_graph.ImportGraph") as MockGraph,
            patch("coderecon.testing.ops.TestOps") as MockTestOps,
        ):
            MockGraph.return_value.affected_tests.return_value = _FakeGraphResult(
                test_files=["tests/test_a.py"]
            )
            mock_ops = MagicMock()
            mock_ops.run = AsyncMock(return_value=mock_test_result)
            MockTestOps.return_value = mock_ops

            result = await run_coverage_for_changed_files(engine, ["src/a.py"])

        mock_ops.run.assert_called_once()
        call_kwargs = mock_ops.run.call_args.kwargs
        assert call_kwargs["coverage"] is True
        assert call_kwargs["targets"] == ["test:tests/test_a.py"]
        assert result == 0  # No coverage artifacts produced

    @pytest.mark.asyncio
    async def test_ingests_coverage_artifacts(self) -> None:
        from coderecon.index.ops_coverage import run_coverage_for_changed_files

        engine = _make_engine(affected_tests=["tests/test_a.py"])

        mock_run_status = MagicMock()
        mock_run_status.coverage = [{"path": "/repo/.recon/artifacts/tests/cov/coverage.xml", "format": "cobertura"}]
        mock_run_status.failures = []
        mock_test_result = MagicMock()
        mock_test_result.run_status = mock_run_status

        with (
            patch("coderecon.index.graph.import_graph.ImportGraph") as MockGraph,
            patch("coderecon.testing.ops.TestOps") as MockTestOps,
            patch("coderecon.testing.coverage.parse_artifact") as mock_parse,
            patch("coderecon.index.analysis.coverage_ingestion.ingest_coverage", return_value=7) as mock_ingest,
        ):
            MockGraph.return_value.affected_tests.return_value = _FakeGraphResult(
                test_files=["tests/test_a.py"]
            )
            mock_ops = MagicMock()
            mock_ops.run = AsyncMock(return_value=mock_test_result)
            MockTestOps.return_value = mock_ops
            mock_parse.return_value = MagicMock()

            result = await run_coverage_for_changed_files(engine, ["src/a.py"])

        assert result == 7
        mock_ingest.assert_called_once()

    @pytest.mark.asyncio
    async def test_never_raises_on_error(self) -> None:
        from coderecon.index.ops_coverage import run_coverage_for_changed_files

        engine = _make_engine()
        with patch("coderecon.index.graph.import_graph.ImportGraph") as MockGraph:
            MockGraph.return_value.affected_tests.side_effect = RuntimeError("graph broke")
            result = await run_coverage_for_changed_files(engine, ["src/a.py"])
        assert result == 0


class TestRunCoverageFull:
    """Full coverage after full reindex."""

    @pytest.mark.asyncio
    async def test_runs_all_tests(self) -> None:
        from coderecon.index.ops_coverage import run_coverage_full

        engine = _make_engine()
        mock_test_result = MagicMock()
        mock_test_result.run_status = None

        with patch("coderecon.testing.ops.TestOps") as MockTestOps:
            mock_ops = MagicMock()
            mock_ops.run = AsyncMock(return_value=mock_test_result)
            MockTestOps.return_value = mock_ops

            result = await run_coverage_full(engine)

        mock_ops.run.assert_called_once()
        call_kwargs = mock_ops.run.call_args.kwargs
        assert call_kwargs["targets"] is None
        assert call_kwargs["coverage"] is True
        assert result == 0

    @pytest.mark.asyncio
    async def test_never_raises_on_error(self) -> None:
        from coderecon.index.ops_coverage import run_coverage_full

        engine = _make_engine()

        with patch("coderecon.testing.ops.TestOps") as MockTestOps:
            MockTestOps.side_effect = RuntimeError("no test runner")
            result = await run_coverage_full(engine)

        assert result == 0

    @pytest.mark.asyncio
    async def test_tracks_failed_tests(self) -> None:
        from coderecon.index.ops_coverage import run_coverage_full

        engine = _make_engine()
        mock_run_status = MagicMock()
        mock_run_status.coverage = [{"path": "/repo/cov.xml", "format": "cobertura"}]
        mock_run_status.failures = [_FakeFailure(path="tests/test_a.py", name="test_foo")]
        mock_test_result = MagicMock()
        mock_test_result.run_status = mock_run_status

        with (
            patch("coderecon.testing.ops.TestOps") as MockTestOps,
            patch("coderecon.testing.coverage.parse_artifact") as mock_parse,
            patch("coderecon.index.analysis.coverage_ingestion.ingest_coverage", return_value=3) as mock_ingest,
        ):
            mock_ops = MagicMock()
            mock_ops.run = AsyncMock(return_value=mock_test_result)
            MockTestOps.return_value = mock_ops
            mock_parse.return_value = MagicMock()

            result = await run_coverage_full(engine)

        assert result == 3
        # Verify failed_test_ids was passed
        call_kwargs = mock_ingest.call_args.kwargs
        assert "tests/test_a.py::test_foo" in call_kwargs["failed_test_ids"]
