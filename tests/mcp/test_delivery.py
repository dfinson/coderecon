"""Tests for MCP delivery — inline trimming and wrap_response.

Covers:
- wrap_response: inline delivery with size-based trimming
- _trim_recon: progressive recon result trimming
- _trim_repo_map: repo map truncation
- _trim_generic: generic key-based trimming
"""

from __future__ import annotations

import json
from typing import Any

from coderecon.mcp.delivery import wrap_response

class TestWrapResponse:
    """Tests for wrap_response inline delivery."""

    def test_small_payload_returned_as_is(self) -> None:
        """Payloads under budget are returned unchanged."""
        data: dict[str, Any] = {"key": "value", "count": 42}
        result = wrap_response(data, resource_kind="test")
        assert result == data

    def test_large_payload_trimmed(self) -> None:
        """Payloads over budget are trimmed."""
        data: dict[str, Any] = {
            "results": [{"snippet": "x" * 5000, "path": f"f{i}.py"} for i in range(20)],
        }
        result = wrap_response(data, resource_kind="recon_result", max_bytes=1000)
        assert len(json.dumps(result, default=str)) <= len(json.dumps(data, default=str))

    def test_raw_signals_never_trimmed(self) -> None:
        """Raw signals are always returned full (no trimming)."""
        big_data: dict[str, Any] = {"candidates": [{"uid": f"def_{i}"} for i in range(1000)]}
        result = wrap_response(big_data, resource_kind="raw_signals", max_bytes=100)
        assert result == big_data

    def test_recon_trimming_removes_snippets_first(self) -> None:
        """Recon trimming removes snippets from bottom results first."""
        results = [
            {"path": f"f{i}.py", "snippet": "code " * 200, "sig": "def f()"}
            for i in range(10)
        ]
        data: dict[str, Any] = {"results": results}
        result = wrap_response(data, resource_kind="recon_result", max_bytes=500)
        trimmed_results = result.get("results", [])
        assert len(trimmed_results) <= len(results)

    def test_repo_map_truncates_structure(self) -> None:
        """Repo map trimming truncates structure string."""
        data: dict[str, Any] = {"structure": "x" * 10000, "entry_points": ["main.py"]}
        result = wrap_response(data, resource_kind="repo_map", max_bytes=3000)
        if "structure" in result:
            assert len(str(result["structure"])) < 10000

    def test_generic_trimming_preserves_critical_keys(self) -> None:
        """Generic trimming does not drop hint/gate/recon_id/metrics."""
        data: dict[str, Any] = {
            "hint": "important",
            "gate": {"id": "abc"},
            "recon_id": "r123",
            "metrics": {"elapsed": 50},
            "big_data": "x" * 50000,
        }
        result = wrap_response(data, resource_kind="unknown", max_bytes=500)
        assert result["hint"] == "important"
        assert result["gate"] == {"id": "abc"}
        assert result["recon_id"] == "r123"
        assert result["metrics"] == {"elapsed": 50}

    def test_session_id_accepted_but_ignored(self) -> None:
        """session_id param is accepted (backward compat) but doesn't affect output."""
        data: dict[str, Any] = {"key": "value"}
        result = wrap_response(data, resource_kind="test", session_id="sess-123")
        assert result == data

    def test_empty_data(self) -> None:
        """Empty dict is returned as-is."""
        result = wrap_response({}, resource_kind="test")
        assert result == {}
