"""Tests for coderecon.testing.coverage.models."""

from __future__ import annotations

import pytest

from coderecon.testing.coverage.models import (
    BranchCoverage,
    CoverageReport,
    FileCoverage,
    FunctionCoverage,
)


class TestFileCoverage:
    """Tests for FileCoverage properties."""

    def test_lines_found(self) -> None:
        fc = FileCoverage(path="a.py", lines={1: 1, 2: 0, 3: 5})
        assert fc.lines_found == 3

    def test_lines_hit(self) -> None:
        fc = FileCoverage(path="a.py", lines={1: 1, 2: 0, 3: 5})
        assert fc.lines_hit == 2

    def test_line_rate(self) -> None:
        fc = FileCoverage(path="a.py", lines={1: 1, 2: 0})
        assert fc.line_rate == pytest.approx(0.5)

    def test_line_rate_empty(self) -> None:
        fc = FileCoverage(path="a.py")
        assert fc.line_rate == 0.0

    def test_uncovered_lines(self) -> None:
        fc = FileCoverage(path="a.py", lines={1: 1, 2: 0, 3: 0, 4: 3})
        assert fc.uncovered_lines == [2, 3]

    def test_branches_found(self) -> None:
        fc = FileCoverage(
            path="a.py",
            branches=[
                BranchCoverage(line=1, block_id=0, branch_id=0, hits=1),
                BranchCoverage(line=1, block_id=0, branch_id=1, hits=0),
            ],
        )
        assert fc.branches_found == 2

    def test_branches_hit(self) -> None:
        fc = FileCoverage(
            path="a.py",
            branches=[
                BranchCoverage(line=1, block_id=0, branch_id=0, hits=1),
                BranchCoverage(line=1, block_id=0, branch_id=1, hits=0),
            ],
        )
        assert fc.branches_hit == 1

    def test_branch_rate(self) -> None:
        fc = FileCoverage(
            path="a.py",
            branches=[
                BranchCoverage(line=1, block_id=0, branch_id=0, hits=1),
                BranchCoverage(line=1, block_id=0, branch_id=1, hits=0),
            ],
        )
        assert fc.branch_rate == pytest.approx(0.5)

    def test_branch_rate_empty(self) -> None:
        fc = FileCoverage(path="a.py")
        assert fc.branch_rate == 0.0

    def test_functions_found(self) -> None:
        fc = FileCoverage(
            path="a.py",
            functions={
                "foo": FunctionCoverage(name="foo", start_line=1, hits=1),
                "bar": FunctionCoverage(name="bar", start_line=5, hits=0),
            },
        )
        assert fc.functions_found == 2

    def test_functions_hit(self) -> None:
        fc = FileCoverage(
            path="a.py",
            functions={
                "foo": FunctionCoverage(name="foo", start_line=1, hits=1),
                "bar": FunctionCoverage(name="bar", start_line=5, hits=0),
            },
        )
        assert fc.functions_hit == 1

    def test_function_rate(self) -> None:
        fc = FileCoverage(
            path="a.py",
            functions={
                "foo": FunctionCoverage(name="foo", start_line=1, hits=2),
                "bar": FunctionCoverage(name="bar", start_line=5, hits=0),
            },
        )
        assert fc.function_rate == pytest.approx(0.5)

    def test_function_rate_empty(self) -> None:
        fc = FileCoverage(path="a.py")
        assert fc.function_rate == 0.0


class TestCoverageReport:
    """Tests for CoverageReport.summary."""

    def test_summary_empty_report(self) -> None:
        report = CoverageReport(source_format="test")
        s = report.summary
        assert s.lines_found == 0
        assert s.lines_hit == 0
        assert s.line_rate == 0.0

    def test_summary_aggregates_files(self) -> None:
        report = CoverageReport(
            source_format="test",
            files={
                "a.py": FileCoverage(path="a.py", lines={1: 1, 2: 0}),
                "b.py": FileCoverage(path="b.py", lines={1: 1, 2: 1}),
            },
        )
        s = report.summary
        assert s.lines_found == 4
        assert s.lines_hit == 3
        assert s.line_rate == pytest.approx(0.75)

    def test_summary_branch_aggregation(self) -> None:
        report = CoverageReport(
            source_format="test",
            files={
                "a.py": FileCoverage(
                    path="a.py",
                    branches=[
                        BranchCoverage(line=1, block_id=0, branch_id=0, hits=1),
                        BranchCoverage(line=1, block_id=0, branch_id=1, hits=0),
                    ],
                ),
            },
        )
        s = report.summary
        assert s.branches_found == 2
        assert s.branches_hit == 1
        assert s.branch_rate == pytest.approx(0.5)

    def test_summary_function_aggregation(self) -> None:
        report = CoverageReport(
            source_format="test",
            files={
                "a.py": FileCoverage(
                    path="a.py",
                    functions={
                        "foo": FunctionCoverage(name="foo", start_line=1, hits=5),
                    },
                ),
                "b.py": FileCoverage(
                    path="b.py",
                    functions={
                        "bar": FunctionCoverage(name="bar", start_line=1, hits=0),
                    },
                ),
            },
        )
        s = report.summary
        assert s.functions_found == 2
        assert s.functions_hit == 1
        assert s.function_rate == pytest.approx(0.5)
