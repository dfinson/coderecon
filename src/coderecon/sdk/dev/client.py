"""Dev SDK client — training-specific extensions over the prod SDK.

Adds methods that the recon-lab pipeline needs but that are NOT
part of the public product surface:

- ``raw_signals`` — full retrieval signal payload for training
- ``index_facts`` — structured index metadata for LLM grounding
- ``lookup_defs`` — def lookup by coordinates (for GT mapping)
- ``index_status`` — file/def counts per worktree
"""

from __future__ import annotations

from typing import Any

from coderecon.sdk.client import CodeRecon
from coderecon.sdk.dev.types import (
    DefEntry,
    IndexFactsResult,
    IndexStatusResult,
    RawSignalsResult,
    _to_def_entries,
    _to_index_facts_result,
    _to_index_status_result,
    _to_raw_signals_result,
)

class CodeReconDev(CodeRecon):
    """Extended SDK for training pipelines.

    Inherits all prod methods (recon, recon_map, etc.) and adds
    training-specific endpoints.
    """

    # ── Training-Specific Methods ──

    async def raw_signals(
        self,
        repo: str,
        query: str,
        *,
        seeds: list[str] | None = None,
        pins: list[str] | None = None,
        worktree: str | None = None,
    ) -> RawSignalsResult:
        """Retrieve full retrieval signals for a query (training data)."""
        params: dict[str, Any] = {"repo": repo, "query": query, "worktree": worktree}
        if seeds:
            params["seeds"] = seeds
        if pins:
            params["pins"] = pins
        return _to_raw_signals_result(await self._tool_call("raw_signals", params))

    async def index_facts(
        self,
        repo: str,
        *,
        worktree: str | None = None,
    ) -> IndexFactsResult:
        """Retrieve structured index metadata for LLM grounding.

        Returns top-level directories, languages, class/function names,
        external dependencies — all from the index, no file I/O.
        """
        return _to_index_facts_result(
            await self._tool_call("index_facts", {"repo": repo, "worktree": worktree})
        )

    async def lookup_defs(
        self,
        repo: str,
        *,
        path: str | None = None,
        name: str | None = None,
        kind: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        worktree: str | None = None,
    ) -> list[DefEntry]:
        """Look up definitions by coordinates.

        All filter params are optional — combine to narrow results.
        Used by the pipeline to map diff hunks to indexed definitions.
        """
        params: dict[str, Any] = {"repo": repo, "worktree": worktree}
        if path is not None:
            params["path"] = path
        if name is not None:
            params["name"] = name
        if kind is not None:
            params["kind"] = kind
        if start_line is not None:
            params["start_line"] = start_line
        if end_line is not None:
            params["end_line"] = end_line
        return _to_def_entries(await self._tool_call("lookup_defs", params))

    async def index_status(
        self,
        repo: str,
        *,
        worktree: str | None = None,
    ) -> IndexStatusResult:
        """Get file/def counts for a repo (or specific worktree).

        Used by the pipeline to check if a worktree is indexed.
        """
        return _to_index_status_result(
            await self._tool_call("index_status", {"repo": repo, "worktree": worktree})
        )
