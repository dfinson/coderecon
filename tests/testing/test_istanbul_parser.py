"""Tests for coderecon.testing.coverage.parsers.istanbul."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coderecon.testing.coverage.models import CoverageParseError
from coderecon.testing.coverage.parsers.istanbul import IstanbulParser

@pytest.fixture()
def parser() -> IstanbulParser:
    return IstanbulParser()

MINIMAL_ISTANBUL = {
    "/src/a.js": {
        "path": "/src/a.js",
        "statementMap": {
            "0": {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 20}},
            "1": {"start": {"line": 2, "column": 0}, "end": {"line": 2, "column": 15}},
        },
        "s": {"0": 1, "1": 0},
        "branchMap": {},
        "b": {},
        "fnMap": {},
        "f": {},
    }
}

class TestFormatId:
    def test_returns_istanbul(self, parser: IstanbulParser) -> None:
        assert parser.format_id == "istanbul"

class TestCanParse:
    def test_directory_with_coverage_final(self, parser: IstanbulParser, tmp_path: Path) -> None:
        (tmp_path / "coverage-final.json").write_text("{}")
        assert parser.can_parse(tmp_path) is True

    def test_directory_without_coverage_final(self, parser: IstanbulParser, tmp_path: Path) -> None:
        assert parser.can_parse(tmp_path) is False

    def test_file_named_coverage_final(self, parser: IstanbulParser, tmp_path: Path) -> None:
        f = tmp_path / "coverage-final.json"
        f.write_text("{}")
        assert parser.can_parse(f) is True

    def test_file_with_statement_map_content(self, parser: IstanbulParser, tmp_path: Path) -> None:
        f = tmp_path / "coverage.json"
        f.write_text(json.dumps(MINIMAL_ISTANBUL))
        assert parser.can_parse(f) is True

    def test_file_without_istanbul_content(self, parser: IstanbulParser, tmp_path: Path) -> None:
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')
        assert parser.can_parse(f) is False

    def test_nonexistent_path(self, parser: IstanbulParser, tmp_path: Path) -> None:
        assert parser.can_parse(tmp_path / "nope") is False

class TestParse:
    def test_parse_minimal(self, parser: IstanbulParser, tmp_path: Path) -> None:
        f = tmp_path / "coverage-final.json"
        f.write_text(json.dumps(MINIMAL_ISTANBUL))
        report = parser.parse(f)
        assert report.source_format == "istanbul"
        assert len(report.files) == 1
        cov = list(report.files.values())[0]
        assert cov.lines[1] == 1
        assert cov.lines[2] == 0

    def test_parse_directory(self, parser: IstanbulParser, tmp_path: Path) -> None:
        (tmp_path / "coverage-final.json").write_text(json.dumps(MINIMAL_ISTANBUL))
        report = parser.parse(tmp_path)
        assert len(report.files) == 1

    def test_parse_missing_file_raises(self, parser: IstanbulParser, tmp_path: Path) -> None:
        with pytest.raises(CoverageParseError, match="not found"):
            parser.parse(tmp_path / "nope.json")

    def test_parse_directory_without_json_raises(self, parser: IstanbulParser, tmp_path: Path) -> None:
        with pytest.raises(CoverageParseError, match="not found"):
            parser.parse(tmp_path)

    def test_parse_invalid_json_raises(self, parser: IstanbulParser, tmp_path: Path) -> None:
        f = tmp_path / "coverage-final.json"
        f.write_text("not json")
        with pytest.raises(CoverageParseError, match="Failed to parse"):
            parser.parse(f)

    def test_parse_with_base_path(self, parser: IstanbulParser, tmp_path: Path) -> None:
        data = {
            "/workspace/src/a.js": {
                "path": "/workspace/src/a.js",
                "statementMap": {"0": {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 20}}},
                "s": {"0": 5},
                "branchMap": {},
                "b": {},
                "fnMap": {},
                "f": {},
            }
        }
        f = tmp_path / "coverage-final.json"
        f.write_text(json.dumps(data))
        report = parser.parse(f, base_path=Path("/workspace"))
        assert "src/a.js" in report.files

    def test_parse_branches(self, parser: IstanbulParser, tmp_path: Path) -> None:
        data = {
            "/src/a.js": {
                "statementMap": {},
                "s": {},
                "branchMap": {
                    "0": {"type": "if", "line": 5, "locations": [
                        {"start": {"line": 5}, "end": {"line": 5}},
                        {"start": {"line": 7}, "end": {"line": 7}},
                    ]},
                },
                "b": {"0": [3, 0]},
                "fnMap": {},
                "f": {},
            }
        }
        f = tmp_path / "coverage-final.json"
        f.write_text(json.dumps(data))
        report = parser.parse(f)
        cov = report.files["/src/a.js"]
        assert len(cov.branches) == 2
        assert cov.branches[0].hits == 3
        assert cov.branches[1].hits == 0

    def test_parse_functions(self, parser: IstanbulParser, tmp_path: Path) -> None:
        data = {
            "/src/a.js": {
                "statementMap": {},
                "s": {},
                "branchMap": {},
                "b": {},
                "fnMap": {
                    "0": {"name": "myFunc", "decl": {"start": {"line": 10, "column": 0}, "end": {"line": 10, "column": 15}}},
                },
                "f": {"0": 7},
            }
        }
        f = tmp_path / "coverage-final.json"
        f.write_text(json.dumps(data))
        report = parser.parse(f)
        cov = report.files["/src/a.js"]
        assert "myFunc" in cov.functions
        assert cov.functions["myFunc"].hits == 7
        assert cov.functions["myFunc"].start_line == 10

    def test_parse_multi_line_statement(self, parser: IstanbulParser, tmp_path: Path) -> None:
        data = {
            "/src/a.js": {
                "statementMap": {
                    "0": {"start": {"line": 1, "column": 0}, "end": {"line": 3, "column": 0}},
                },
                "s": {"0": 2},
                "branchMap": {},
                "b": {},
                "fnMap": {},
                "f": {},
            }
        }
        f = tmp_path / "coverage-final.json"
        f.write_text(json.dumps(data))
        report = parser.parse(f)
        cov = report.files["/src/a.js"]
        # Lines 1-3 should all have hits
        assert cov.lines[1] == 2
        assert cov.lines[2] == 2
        assert cov.lines[3] == 2
