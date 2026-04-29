"""SARIF emitter — convert gate violations and lint diagnostics to SARIF 2.1.0.

SARIF (Static Analysis Results Interchange Format) enables IDE integration.
Output can be consumed by VS Code SARIF Viewer, GitHub Code Scanning, etc.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from coderecon.adapters.files.ops import atomic_write_text

if TYPE_CHECKING:
    from coderecon.index.analysis.gate_engine import GateResult

# SARIF 2.1.0 schema
_SARIF_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
_SARIF_VERSION = "2.1.0"

_LEVEL_MAP = {
    "error": "error",
    "warning": "warning",
    "info": "note",
}

def gate_result_to_sarif(
    gate_result: GateResult,
    *,
    tool_name: str = "coderecon-governance",
    tool_version: str = "1.0.0",
) -> dict[str, Any]:
    """Convert a GateResult to SARIF 2.1.0 format.

    Args:
        gate_result: Output from evaluate_gates().
        tool_name: Name of the reporting tool.
        tool_version: Version of the reporting tool.

    Returns:
        SARIF 2.1.0 compliant dict.
    """
    results = []
    rules = []
    rule_index: dict[str, int] = {}

    for violation in gate_result.violations:
        # Register rule if not seen
        if violation.rule not in rule_index:
            rule_index[violation.rule] = len(rules)
            rules.append({
                "id": f"governance/{violation.rule}",
                "shortDescription": {"text": violation.rule.replace("_", " ").title()},
            })

        sarif_result: dict[str, Any] = {
            "ruleId": f"governance/{violation.rule}",
            "ruleIndex": rule_index[violation.rule],
            "level": _LEVEL_MAP.get(violation.level, "note"),
            "message": {"text": violation.message},
        }

        # Add file locations from details if available
        if violation.details:
            files = violation.details.get("involved_files") or violation.details.get("files")
            if files and isinstance(files, list):
                locations = []
                for f in files[:5]:
                    file_path = f.get("source", f) if isinstance(f, dict) else str(f)
                    locations.append({
                        "physicalLocation": {
                            "artifactLocation": {"uri": file_path},
                        },
                    })
                if locations:
                    sarif_result["locations"] = locations

        results.append(sarif_result)

    return {
        "$schema": _SARIF_SCHEMA,
        "version": _SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": tool_name,
                        "version": tool_version,
                        "rules": rules,
                    },
                },
                "results": results,
            },
        ],
    }

def lint_diagnostics_to_sarif(
    diagnostics: list[dict[str, Any]],
    *,
    tool_name: str = "coderecon-lint",
    tool_version: str = "1.0.0",
) -> dict[str, Any]:
    """Convert lint diagnostics to SARIF 2.1.0.

    Args:
        diagnostics: List of dicts with path, line, column, severity, code, message.

    Returns:
        SARIF 2.1.0 compliant dict.
    """
    results = []
    rules = []
    rule_index: dict[str, int] = {}

    for diag in diagnostics:
        code = diag.get("code", "unknown")
        rule_id = f"lint/{code}"

        if rule_id not in rule_index:
            rule_index[rule_id] = len(rules)
            rules.append({
                "id": rule_id,
                "shortDescription": {"text": diag.get("message", code)[:80]},
            })

        sev = diag.get("severity", "warning")
        level = _LEVEL_MAP.get(sev, "note")

        sarif_result: dict[str, Any] = {
            "ruleId": rule_id,
            "ruleIndex": rule_index[rule_id],
            "level": level,
            "message": {"text": diag.get("message", "")},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": diag.get("path", "")},
                        "region": {
                            "startLine": diag.get("line", 1),
                            "startColumn": diag.get("column", 1),
                        },
                    },
                },
            ],
        }
        results.append(sarif_result)

    return {
        "$schema": _SARIF_SCHEMA,
        "version": _SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": tool_name,
                        "version": tool_version,
                        "rules": rules,
                    },
                },
                "results": results,
            },
        ],
    }

def write_sarif(sarif: dict[str, Any], output_path: Path) -> Path:
    """Write SARIF dict to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, json.dumps(sarif, indent=2))
    return output_path
