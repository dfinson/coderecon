"""Result dataclasses for import-graph queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class ImpactMatch:
    """A single test file matched by the import graph."""
    test_file: str
    source_modules: list[str]
    confidence: Literal["high", "low"]
    reason: str
    hop: int = 0

@dataclass
class ImpactConfidence:
    """Confidence assessment for an import graph query."""
    tier: Literal["complete", "partial"]
    resolved_ratio: float
    unresolved_files: list[str]
    null_source_count: int
    reasoning: str

@dataclass
class ImportGraphResult:
    """Result of an affected_tests query."""
    matches: list[ImpactMatch]
    confidence: ImpactConfidence
    changed_modules: list[str]
    @property
    def test_files(self) -> list[str]:
        """All test file paths (convenience)."""
        return [m.test_file for m in self.matches]
    @property
    def high_confidence_tests(self) -> list[str]:
        return [m.test_file for m in self.matches if m.confidence == "high"]
    @property
    def low_confidence_tests(self) -> list[str]:
        return [m.test_file for m in self.matches if m.confidence == "low"]
    @property
    def max_hop(self) -> int:
        """Highest hop distance among all matches."""
        return max((m.hop for m in self.matches), default=0)
    def tests_by_hop(self) -> dict[int, list[str]]:
        """Group test file paths by hop distance."""
        result: dict[int, list[str]] = {}
        for m in self.matches:
            result.setdefault(m.hop, []).append(m.test_file)
        return result

@dataclass
class CoverageSourceResult:
    """Result of an imported_sources query."""
    source_dirs: list[str]
    source_modules: list[str]
    confidence: Literal["complete", "partial"]
    null_import_count: int

@dataclass
class CoverageGap:
    """A source module with no test imports."""
    module: str
    file_path: str | None
