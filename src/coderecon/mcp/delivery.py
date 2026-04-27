"""Inline-only delivery for MCP tool responses.

All responses are returned inline. If a response exceeds the inline
budget (30KB), it is trimmed per resource_kind and a hint is added.
No sidecar cache, no disk I/O, no jq commands.
"""

from __future__ import annotations

import json
from typing import Any

from coderecon.config.constants import INLINE_CAP_BYTES

_STRUCTURE_TRUNCATION_CHARS = 2000  # keep structure section readable in MCP responses

def wrap_response(
    data: dict[str, Any],
    *,
    resource_kind: str = "",
    session_id: str | None = None,
    max_bytes: int = INLINE_CAP_BYTES,
) -> dict[str, Any]:
    """Serialize and return inline. Trim if over budget.

    Args:
        data: The response dict.
        resource_kind: Used to decide trimming strategy.
        session_id: Ignored (no session-scoped caching).
        max_bytes: Inline byte budget.

    Returns:
        The response dict, possibly trimmed.
    """
    serialized = json.dumps(data, default=str)
    if resource_kind == "raw_signals" or len(serialized) <= max_bytes:
        return data

    # Trim based on resource kind
    trimmed = _trim(data, resource_kind, max_bytes)
    return trimmed

def _trim(data: dict[str, Any], kind: str, budget: int) -> dict[str, Any]:
    """Trim response to fit within budget."""
    if kind == "recon_result":
        return _trim_recon(data, budget)
    if kind == "repo_map":
        return _trim_repo_map(data, budget)
    if kind == "raw_signals":
        return _trim_raw_signals(data, budget)
    # Generic: drop largest key until it fits
    return _trim_generic(data, budget)

def _trim_recon(data: dict[str, Any], budget: int) -> dict[str, Any]:
    """Trim recon results by removing snippets from bottom results first."""
    results = data.get("results", [])
    if not results:
        return data

    # Progressive trimming: remove sig/snippet from bottom up
    trimmed = {**data, "results": list(results)}
    current_size = len(json.dumps(trimmed, default=str))

    for i in range(len(trimmed["results"]) - 1, -1, -1):
        if current_size <= budget:
            break
        r = trimmed["results"][i]
        old_r_size = len(json.dumps(r, default=str))
        r_stripped = {k: v for k, v in r.items() if k not in ("snippet", "sig")}
        new_r_size = len(json.dumps(r_stripped, default=str))
        trimmed["results"][i] = r_stripped
        current_size += new_r_size - old_r_size

    # Still too big? Remove results from bottom
    while current_size > budget and trimmed["results"]:
        removed = trimmed["results"].pop()
        # Subtract element size + JSON comma/space overhead
        current_size -= len(json.dumps(removed, default=str)) + 2

    if len(trimmed["results"]) < len(results):
        trimmed["hint"] = (
            trimmed.get("hint", "") +
            f" Trimmed to {len(trimmed['results'])}/{len(results)} results to fit inline."
        ).strip()

    return trimmed

def _trim_repo_map(data: dict[str, Any], budget: int) -> dict[str, Any]:
    """Trim repo map by truncating structure."""
    trimmed = dict(data)
    # Drop structure first (largest section)
    if "structure" in trimmed and len(json.dumps(trimmed, default=str)) > budget:
        struct = trimmed["structure"]
        if isinstance(struct, str) and len(struct) > _STRUCTURE_TRUNCATION_CHARS:
            trimmed["structure"] = struct[:_STRUCTURE_TRUNCATION_CHARS] + "\n... (truncated)"
    # Drop entry_points if still too big
    if len(json.dumps(trimmed, default=str)) > budget:
        trimmed.pop("entry_points", None)
    return trimmed

def _trim_raw_signals(data: dict[str, Any], budget: int) -> dict[str, Any]:
    """Raw signals has no budget — always return full inline."""
    return data

def _trim_generic(data: dict[str, Any], budget: int) -> dict[str, Any]:
    """Generic trimming: drop largest values until it fits."""
    trimmed = dict(data)
    # Pre-compute key sizes for sorting (one json.dumps per key)
    key_sizes = {k: len(json.dumps(v, default=str)) for k, v in trimmed.items()}
    keys_by_size = sorted(key_sizes, key=key_sizes.get, reverse=True)  # type: ignore[arg-type]

    current_size = len(json.dumps(trimmed, default=str))
    for key in keys_by_size:
        if current_size <= budget:
            break
        if key in ("hint", "gate", "recon_id", "metrics"):
            continue  # don't drop critical keys
        replacement = f"(trimmed — {key} too large for inline)"
        new_val_size = len(json.dumps(replacement, default=str))
        current_size += new_val_size - key_sizes[key]
        trimmed[key] = replacement
    return trimmed
