"""SDK result types — thin frozen dataclasses for typed tool results.

These are the public result objects returned by the SDK. They wrap the
raw ``dict`` payloads from the daemon into typed structures. Fields
mirror the JSON wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class CodeSpan:
    file: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None = None
    score: float = 0.0

@dataclass(frozen=True)
class ReconResult:
    recon_id: str
    gate: str = "OK"
    results: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    hint: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class MapResult:
    overview: str = ""
    sections: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class ImpactResult:
    references: list[dict[str, Any]] = field(default_factory=list)
    total_references: int = 0
    files_affected: int = 0
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class UnderstandResult:
    sections: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class RefactorResult:
    refactor_id: str = ""
    status: str = ""
    preview: dict[str, Any] | None = None
    agentic_hint: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class RefactorCommitResult:
    refactor_id: str = ""
    status: str = ""
    applied: bool = False
    files_modified: list[str] = field(default_factory=list)
    inspection: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class RefactorCancelResult:
    refactor_id: str = ""
    cancelled: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class DiffResult:
    summary: str = ""
    structural_changes: list[dict[str, Any]] = field(default_factory=list)
    scope: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class CyclesResult:
    level: str = "file"
    cycles: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class CommunitiesResult:
    level: str = "file"
    communities: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class GraphExportResult:
    path: str = ""
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class CheckpointResult:
    passed: bool = False
    lint: dict[str, Any] | None = None
    tests: dict[str, Any] | None = None
    commit: dict[str, Any] | None = None
    summary: str = ""
    agentic_hint: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class DescribeResult:
    found: bool = False
    description: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class RegisterResult:
    repo: str = ""
    worktree: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class CatalogEntry:
    name: str = ""
    git_dir: str = ""
    worktrees: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class StatusResult:
    daemon_healthy: bool = False
    active_repos: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class RawSignalsResult:
    query_features: dict[str, Any] = field(default_factory=dict)
    repo_features: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0

# Wire → typed conversion

def _to_recon_result(d: dict[str, Any]) -> ReconResult:
    return ReconResult(
        recon_id=d.get("recon_id", ""),
        gate=d.get("gate", "OK"),
        results=d.get("results", []),
        metrics=d.get("metrics", {}),
        hint=d.get("hint", ""),
        raw=d,
    )

def _to_map_result(d: dict[str, Any]) -> MapResult:
    return MapResult(overview=d.get("overview", ""), sections=d, raw=d)

def _to_impact_result(d: dict[str, Any]) -> ImpactResult:
    return ImpactResult(
        references=d.get("references", []),
        total_references=d.get("total_references", 0),
        files_affected=d.get("files_affected", 0),
        summary=d.get("summary", ""),
        raw=d,
    )

def _to_understand_result(d: dict[str, Any]) -> UnderstandResult:
    return UnderstandResult(
        sections=d.get("sections", {}),
        summary=d.get("summary", ""),
        raw=d,
    )

def _to_refactor_result(d: dict[str, Any]) -> RefactorResult:
    return RefactorResult(
        refactor_id=d.get("refactor_id", ""),
        status=d.get("status", ""),
        preview=d.get("preview"),
        agentic_hint=d.get("agentic_hint", ""),
        raw=d,
    )

def _to_refactor_commit_result(d: dict[str, Any]) -> RefactorCommitResult:
    return RefactorCommitResult(
        refactor_id=d.get("refactor_id", ""),
        status=d.get("status", ""),
        applied=d.get("status") == "applied",
        files_modified=[],
        inspection=d.get("matches"),
        raw=d,
    )

def _to_refactor_cancel_result(d: dict[str, Any]) -> RefactorCancelResult:
    return RefactorCancelResult(
        refactor_id=d.get("refactor_id", ""),
        cancelled=d.get("status") == "cancelled",
        raw=d,
    )

def _to_diff_result(d: dict[str, Any]) -> DiffResult:
    return DiffResult(
        summary=d.get("summary", ""),
        structural_changes=d.get("structural_changes", []),
        scope=d.get("scope"),
        raw=d,
    )

def _to_cycles_result(d: dict[str, Any]) -> CyclesResult:
    return CyclesResult(
        level=d.get("level", "file"),
        cycles=d.get("cycles", []),
        summary=d.get("summary", ""),
        raw=d,
    )

def _to_communities_result(d: dict[str, Any]) -> CommunitiesResult:
    return CommunitiesResult(
        level=d.get("level", "file"),
        communities=d.get("communities", []),
        summary=d.get("summary", ""),
        raw=d,
    )

def _to_graph_export_result(d: dict[str, Any]) -> GraphExportResult:
    return GraphExportResult(
        path=d.get("path", ""),
        message=d.get("message", ""),
        raw=d,
    )

def _to_checkpoint_result(d: dict[str, Any]) -> CheckpointResult:
    return CheckpointResult(
        passed=d.get("passed", False),
        lint=d.get("lint"),
        tests=d.get("tests"),
        commit=d.get("commit"),
        summary=d.get("summary", ""),
        agentic_hint=d.get("agentic_hint", ""),
        raw=d,
    )

def _to_describe_result(d: dict[str, Any]) -> DescribeResult:
    return DescribeResult(
        found=d.get("found", False),
        description=d.get("description"),
        raw=d,
    )

def _to_register_result(d: dict[str, Any]) -> RegisterResult:
    return RegisterResult(repo=d.get("repo", ""), worktree=d.get("worktree", ""), raw=d)

def _to_status_result(d: dict[str, Any]) -> StatusResult:
    return StatusResult(
        daemon_healthy=d.get("daemon_healthy", False),
        active_repos=d.get("active_repos", []),
        raw=d,
    )

def _to_raw_signals_result(d: dict[str, Any]) -> RawSignalsResult:
    return RawSignalsResult(
        query_features=d.get("query_features", {}),
        repo_features=d.get("repo_features", {}),
        candidates=d.get("candidates", []),
        diagnostics=d.get("diagnostics", {}),
        raw=d,
    )
