"""Output parsers for lint tools."""
from __future__ import annotations

import json
import re

from coderecon.lint.models import Diagnostic, ParseResult, Severity


def _severity_from_str(s: str) -> Severity:
    """Convert string to Severity."""
    s = s.lower()
    if s in ("error", "e", "fatal"):
        return Severity.ERROR
    if s in ("warning", "warn", "w"):
        return Severity.WARNING
    if s in ("info", "information", "i", "note"):
        return Severity.INFO
    return Severity.HINT


# Generic Parsers


def parse_sarif(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse SARIF format (Static Analysis Results Interchange Format)."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for run in data.get("runs", []):
            tool_name = run.get("tool", {}).get("driver", {}).get("name", "unknown")
            run.get("tool", {}).get("driver", {}).get("rules", [])  # available for future use
            for result in run.get("results", []):
                rule_id = result.get("ruleId", "")
                level = result.get("level", "warning")
                message = result.get("message", {}).get("text", "")
                for location in result.get("locations", []):
                    phys = location.get("physicalLocation", {})
                    artifact = phys.get("artifactLocation", {})
                    region = phys.get("region", {})
                    diagnostics.append(
                        Diagnostic(
                            path=artifact.get("uri", ""),
                            line=region.get("startLine", 1),
                            column=region.get("startColumn"),
                            end_line=region.get("endLine"),
                            end_column=region.get("endColumn"),
                            severity=_severity_from_str(level),
                            code=rule_id,
                            message=message,
                            source=tool_name,
                        )
                    )
    except (json.JSONDecodeError, KeyError) as e:
        return ParseResult.error(f"SARIF parse error: {e}")
    return ParseResult.ok(diagnostics)


# Python Parsers


def parse_ruff(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse ruff JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for item in data:
            diagnostics.append(
                Diagnostic(
                    path=item.get("filename", ""),
                    line=item.get("location", {}).get("row", 1),
                    column=item.get("location", {}).get("column"),
                    end_line=item.get("end_location", {}).get("row"),
                    end_column=item.get("end_location", {}).get("column"),
                    severity=Severity.WARNING,
                    code=item.get("code"),
                    message=item.get("message", ""),
                    source="ruff",
                    fix_applied=item.get("fix") is not None
                    and item.get("fix", {}).get("applicability") == "safe",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Ruff JSON parse error: {e}")
    return ParseResult.ok(diagnostics)

def parse_mypy(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse mypy JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        for line in stdout.strip().split("\n"):
            if not line.strip():
                continue
            data = json.loads(line)
            diagnostics.append(
                Diagnostic(
                    path=data.get("file", ""),
                    line=data.get("line", 1),
                    column=data.get("column"),
                    severity=_severity_from_str(data.get("severity", "error")),
                    code=data.get("code"),
                    message=data.get("message", ""),
                    source="mypy",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Mypy JSON parse error: {e}")
    return ParseResult.ok(diagnostics)

def parse_pyright(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse pyright JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for diag in data.get("generalDiagnostics", []):
            rng = diag.get("range", {})
            start = rng.get("start", {})
            end = rng.get("end", {})
            diagnostics.append(
                Diagnostic(
                    path=diag.get("file", ""),
                    line=start.get("line", 0) + 1,  # 0-indexed
                    column=start.get("character", 0) + 1,
                    end_line=end.get("line", 0) + 1 if end else None,
                    end_column=end.get("character", 0) + 1 if end else None,
                    severity=_severity_from_str(diag.get("severity", "error")),
                    code=diag.get("rule"),
                    message=diag.get("message", ""),
                    source="pyright",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Pyright JSON parse error: {e}")
    return ParseResult.ok(diagnostics)

def parse_bandit(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse bandit JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for result in data.get("results", []):
            sev = result.get("issue_severity", "MEDIUM")
            diagnostics.append(
                Diagnostic(
                    path=result.get("filename", ""),
                    line=result.get("line_number", 1),
                    severity=Severity.ERROR if sev == "HIGH" else Severity.WARNING,
                    code=result.get("test_id"),
                    message=f"{result.get('issue_text', '')} (confidence: {result.get('issue_confidence', 'MEDIUM')})",
                    source="bandit",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Bandit JSON parse error: {e}")
    return ParseResult.ok(diagnostics)

def parse_black_check(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse black --check output."""
    diagnostics: list[Diagnostic] = []
    # Black writes to stderr: "would reformat path/to/file.py"
    for line in stderr.strip().split("\n"):
        if line.startswith("would reformat "):
            path = line[len("would reformat ") :]
            diagnostics.append(
                Diagnostic(
                    path=path,
                    line=1,
                    severity=Severity.WARNING,
                    message="File would be reformatted",
                    source="black",
                )
            )
    return ParseResult.ok(diagnostics)


# JavaScript/TypeScript Parsers


def parse_eslint(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse eslint JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for file_result in data:
            filepath = file_result.get("filePath", "")
            for msg in file_result.get("messages", []):
                diagnostics.append(
                    Diagnostic(
                        path=filepath,
                        line=msg.get("line", 1),
                        column=msg.get("column"),
                        end_line=msg.get("endLine"),
                        end_column=msg.get("endColumn"),
                        severity=Severity.ERROR if msg.get("severity") == 2 else Severity.WARNING,
                        code=msg.get("ruleId"),
                        message=msg.get("message", ""),
                        source="eslint",
                        fix_applied=msg.get("fix") is not None,
                    )
                )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"ESLint JSON parse error: {e}")
    return ParseResult.ok(diagnostics)

def parse_tsc(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse tsc output (line-based format)."""
    diagnostics: list[Diagnostic] = []
    # Format: file(line,col): error TSxxxx: message
    pattern = re.compile(r"^(.+?)\((\d+),(\d+)\): (error|warning) (TS\d+): (.+)$")
    for line in stdout.strip().split("\n"):
        match = pattern.match(line)
        if match:
            diagnostics.append(
                Diagnostic(
                    path=match.group(1),
                    line=int(match.group(2)),
                    column=int(match.group(3)),
                    severity=_severity_from_str(match.group(4)),
                    code=match.group(5),
                    message=match.group(6),
                    source="tsc",
                )
            )
    return ParseResult.ok(diagnostics)

def parse_prettier_check(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse prettier --check output."""
    diagnostics: list[Diagnostic] = []
    # Prettier lists files that need formatting, one per line
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("Checking") and not line.startswith("All "):
            diagnostics.append(
                Diagnostic(
                    path=line,
                    line=1,
                    severity=Severity.WARNING,
                    message="File needs formatting",
                    source="prettier",
                )
            )
    return ParseResult.ok(diagnostics)

def parse_biome(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse biome JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for diag in data.get("diagnostics", []):
            location = diag.get("location", {})
            span = location.get("span", [0, 0])
            diagnostics.append(
                Diagnostic(
                    path=location.get("path", {}).get("file", ""),
                    line=diag.get("line", 1),
                    column=span[0] if span else None,
                    severity=_severity_from_str(diag.get("severity", "error")),
                    code=diag.get("category"),
                    message=diag.get("message", {}).get("text", "")
                    if isinstance(diag.get("message"), dict)
                    else str(diag.get("message", "")),
                    source="biome",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Biome JSON parse error: {e}")
    return ParseResult.ok(diagnostics)

# Re-export extended parsers for convenience
from coderecon.lint.parsers_ext import (  # noqa: E402, F401
    parse_cargo_audit,
    parse_checkstyle,
    parse_clippy,
    parse_dotnet_format,
    parse_go_vet,
    parse_gofmt,
    parse_golangci_lint,
    parse_hadolint,
    parse_ktlint,
    parse_markdownlint,
    parse_phpcs,
    parse_phpstan,
    parse_rubocop,
    parse_ruff_format,
    parse_rustfmt_check,
    parse_shellcheck,
    parse_shfmt,
    parse_sqlfluff,
    parse_staticcheck,
    parse_yamllint,
)


