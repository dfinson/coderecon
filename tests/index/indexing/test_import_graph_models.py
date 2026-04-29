"""Tests for import_graph_models dataclasses."""

from __future__ import annotations

from coderecon.index.graph.import_graph_models import (
    CoverageGap,
    CoverageSourceResult,
    ImpactConfidence,
    ImpactMatch,
    ImportGraphResult,
)


class TestImpactMatch:
    def test_fields(self) -> None:
        m = ImpactMatch(
            test_file="tests/test_foo.py",
            source_modules=["src.foo"],
            confidence="high",
            reason="direct import",
            hop=1,
        )
        assert m.test_file == "tests/test_foo.py"
        assert m.source_modules == ["src.foo"]
        assert m.confidence == "high"
        assert m.reason == "direct import"
        assert m.hop == 1

    def test_default_hop(self) -> None:
        m = ImpactMatch(
            test_file="t.py", source_modules=[], confidence="low", reason="r"
        )
        assert m.hop == 0


class TestImportGraphResult:
    def _make_result(self) -> ImportGraphResult:
        return ImportGraphResult(
            matches=[
                ImpactMatch("tests/a.py", ["m1"], "high", "direct", hop=1),
                ImpactMatch("tests/b.py", ["m2"], "low", "transitive", hop=2),
                ImpactMatch("tests/c.py", ["m3"], "high", "direct", hop=1),
            ],
            confidence=ImpactConfidence(
                tier="complete",
                resolved_ratio=1.0,
                unresolved_files=[],
                null_source_count=0,
                reasoning="all resolved",
            ),
            changed_modules=["m1", "m2"],
        )

    def test_test_files(self) -> None:
        r = self._make_result()
        assert r.test_files == ["tests/a.py", "tests/b.py", "tests/c.py"]

    def test_high_confidence_tests(self) -> None:
        r = self._make_result()
        assert r.high_confidence_tests == ["tests/a.py", "tests/c.py"]

    def test_low_confidence_tests(self) -> None:
        r = self._make_result()
        assert r.low_confidence_tests == ["tests/b.py"]

    def test_max_hop(self) -> None:
        r = self._make_result()
        assert r.max_hop == 2

    def test_max_hop_empty(self) -> None:
        r = ImportGraphResult(
            matches=[],
            confidence=ImpactConfidence("partial", 0.0, [], 0, ""),
            changed_modules=[],
        )
        assert r.max_hop == 0

    def test_tests_by_hop(self) -> None:
        r = self._make_result()
        by_hop = r.tests_by_hop()
        assert set(by_hop[1]) == {"tests/a.py", "tests/c.py"}
        assert by_hop[2] == ["tests/b.py"]


class TestCoverageSourceResult:
    def test_fields(self) -> None:
        r = CoverageSourceResult(
            source_dirs=["src/"],
            source_modules=["foo", "bar"],
            confidence="complete",
            null_import_count=0,
        )
        assert r.confidence == "complete"
        assert len(r.source_modules) == 2


class TestCoverageGap:
    def test_fields(self) -> None:
        g = CoverageGap(module="foo.bar", file_path="src/foo/bar.py")
        assert g.module == "foo.bar"
        assert g.file_path == "src/foo/bar.py"

    def test_none_file_path(self) -> None:
        g = CoverageGap(module="x", file_path=None)
        assert g.file_path is None
