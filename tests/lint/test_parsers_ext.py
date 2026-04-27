"""Tests for lint/parsers_ext.py — extended output parsers."""
from __future__ import annotations

import json

from coderecon.lint.models import Severity
from coderecon.lint.parsers_ext import (
    parse_clippy,
    parse_go_vet,
    parse_hadolint,
    parse_rubocop,
    parse_shellcheck,
    parse_yamllint,
)


class TestParseGoVet:
    """Tests for parse_go_vet."""

    def test_empty_output(self) -> None:
        result = parse_go_vet("", "")
        assert result.diagnostics == []
        assert result.success

    def test_single_diagnostic(self) -> None:
        output = "main.go:42:5: unreachable code"
        result = parse_go_vet(output, "")
        assert len(result.diagnostics) == 1
        d = result.diagnostics[0]
        assert d.path == "main.go"
        assert d.line == 42
        assert d.column == 5
        assert d.message == "unreachable code"
        assert d.severity == Severity.WARNING
        assert d.source == "go vet"

    def test_multiple_diagnostics(self) -> None:
        output = "a.go:1:2: msg one\nb.go:10:20: msg two"
        result = parse_go_vet(output, "")
        assert len(result.diagnostics) == 2
        assert result.diagnostics[0].path == "a.go"
        assert result.diagnostics[1].path == "b.go"

    def test_reads_from_stderr(self) -> None:
        result = parse_go_vet("", "pkg/x.go:5:1: shadow variable")
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].message == "shadow variable"

    def test_non_matching_lines_ignored(self) -> None:
        output = "# some header\nmain.go:1:1: real\n"
        result = parse_go_vet(output, "")
        assert len(result.diagnostics) == 1


class TestParseClippy:
    """Tests for parse_clippy (Rust clippy JSON output)."""

    def _make_clippy_json(
        self,
        *,
        file_name: str = "src/lib.rs",
        line_start: int = 10,
        column_start: int = 5,
        level: str = "warning",
        message: str = "unused variable",
        code: str | None = "unused_variables",
    ) -> str:
        span = {
            "file_name": file_name,
            "line_start": line_start,
            "column_start": column_start,
            "line_end": line_start,
            "column_end": column_start + 3,
            "is_primary": True,
        }
        msg: dict = {
            "level": level,
            "message": message,
            "spans": [span],
        }
        if code:
            msg["code"] = {"code": code}
        return json.dumps({"reason": "compiler-message", "message": msg})

    def test_empty_output(self) -> None:
        result = parse_clippy("", "")
        assert result.diagnostics == []
        assert result.success

    def test_single_warning(self) -> None:
        line = self._make_clippy_json()
        result = parse_clippy(line, "")
        assert len(result.diagnostics) == 1
        d = result.diagnostics[0]
        assert d.path == "src/lib.rs"
        assert d.line == 10
        assert d.column == 5
        assert d.severity == Severity.WARNING
        assert d.code == "unused_variables"
        assert d.source == "clippy"

    def test_error_level(self) -> None:
        line = self._make_clippy_json(level="error", code="E0308")
        result = parse_clippy(line, "")
        assert result.diagnostics[0].severity == Severity.ERROR

    def test_non_compiler_message_skipped(self) -> None:
        line = json.dumps({"reason": "build-script-executed"})
        result = parse_clippy(line, "")
        assert result.diagnostics == []

    def test_invalid_json(self) -> None:
        result = parse_clippy("{bad json", "")
        assert not result.success
        assert "JSON parse error" in (result.parse_error or "")


class TestParseShellcheck:
    """Tests for parse_shellcheck (JSON output)."""

    def _make_shellcheck_json(
        self,
        *,
        file: str = "script.sh",
        line: int = 5,
        column: int = 10,
        level: str = "warning",
        code: int = 2086,
        message: str = "Double quote to prevent globbing",
    ) -> str:
        return json.dumps([{
            "file": file,
            "line": line,
            "column": column,
            "endLine": line,
            "endColumn": column + 5,
            "level": level,
            "code": code,
            "message": message,
            "fix": None,
        }])

    def test_empty_array(self) -> None:
        result = parse_shellcheck("[]", "")
        assert result.diagnostics == []
        assert result.success

    def test_single_warning(self) -> None:
        data = self._make_shellcheck_json()
        result = parse_shellcheck(data, "")
        assert len(result.diagnostics) == 1
        d = result.diagnostics[0]
        assert d.path == "script.sh"
        assert d.line == 5
        assert d.column == 10
        assert d.severity == Severity.WARNING
        assert d.code == "SC2086"
        assert d.source == "shellcheck"

    def test_error_level(self) -> None:
        data = self._make_shellcheck_json(level="error", code=1091)
        result = parse_shellcheck(data, "")
        assert result.diagnostics[0].severity == Severity.ERROR
        assert result.diagnostics[0].code == "SC1091"

    def test_fix_applied_flag(self) -> None:
        item = {
            "file": "x.sh", "line": 1, "column": 1,
            "level": "info", "code": 1000, "message": "m",
            "fix": {"replacements": []},
        }
        result = parse_shellcheck(json.dumps([item]), "")
        assert result.diagnostics[0].fix_applied is True

    def test_invalid_json(self) -> None:
        result = parse_shellcheck("not json", "")
        assert not result.success


class TestParseRubocop:
    """Tests for parse_rubocop (JSON output)."""

    def _make_rubocop_json(self) -> str:
        return json.dumps({
            "files": [{
                "path": "app.rb",
                "offenses": [{
                    "location": {
                        "start_line": 3,
                        "start_column": 1,
                        "last_line": 3,
                        "last_column": 20,
                    },
                    "severity": "convention",
                    "cop_name": "Style/FrozenStringLiteralComment",
                    "message": "Missing frozen string literal comment",
                    "corrected": False,
                }],
            }],
        })

    def test_empty_files(self) -> None:
        result = parse_rubocop(json.dumps({"files": []}), "")
        assert result.diagnostics == []

    def test_single_offense(self) -> None:
        result = parse_rubocop(self._make_rubocop_json(), "")
        assert len(result.diagnostics) == 1
        d = result.diagnostics[0]
        assert d.path == "app.rb"
        assert d.line == 3
        assert d.column == 1
        assert d.end_line == 3
        assert d.end_column == 20
        assert d.code == "Style/FrozenStringLiteralComment"
        assert d.source == "rubocop"
        assert d.fix_applied is False

    def test_invalid_json(self) -> None:
        result = parse_rubocop("{{", "")
        assert not result.success


class TestParseHadolint:
    """Tests for parse_hadolint (JSON output)."""

    def _make_hadolint_json(self) -> str:
        return json.dumps([{
            "file": "Dockerfile",
            "line": 7,
            "column": 1,
            "level": "warning",
            "code": "DL3008",
            "message": "Pin versions in apt get install",
        }])

    def test_empty_array(self) -> None:
        result = parse_hadolint("[]", "")
        assert result.diagnostics == []

    def test_single_diagnostic(self) -> None:
        result = parse_hadolint(self._make_hadolint_json(), "")
        assert len(result.diagnostics) == 1
        d = result.diagnostics[0]
        assert d.path == "Dockerfile"
        assert d.line == 7
        assert d.code == "DL3008"
        assert d.severity == Severity.WARNING
        assert d.source == "hadolint"

    def test_invalid_json(self) -> None:
        result = parse_hadolint("bad", "")
        assert not result.success


class TestParseYamllint:
    """Tests for parse_yamllint (parsable text output)."""

    def test_empty_output(self) -> None:
        result = parse_yamllint("", "")
        assert result.diagnostics == []

    def test_single_diagnostic(self) -> None:
        output = "config.yaml:3:5: [warning] too many spaces (indentation)"
        result = parse_yamllint(output, "")
        assert len(result.diagnostics) == 1
        d = result.diagnostics[0]
        assert d.path == "config.yaml"
        assert d.line == 3
        assert d.column == 5
        assert d.severity == Severity.WARNING
        assert d.source == "yamllint"

    def test_error_level(self) -> None:
        output = "a.yml:1:1: [error] syntax error"
        result = parse_yamllint(output, "")
        assert result.diagnostics[0].severity == Severity.ERROR
