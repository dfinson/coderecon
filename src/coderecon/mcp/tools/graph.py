"""Graph analysis MCP tools — cycles, communities, similarity, understand.

Exposes graph algorithms, def-to-def similarity, and a narrative
briefing as MCP tools.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from sqlalchemy.engine import Engine

    from coderecon.index._internal.analysis.code_graph import GraphAnalysisResult
    from coderecon.mcp.context import AppContext

log = structlog.get_logger(__name__)

# Core Functions (transport-agnostic)

def graph_cycles_core(
    app_ctx: AppContext,
    *,
    level: str = "file",
) -> dict[str, Any]:
    """Detect circular dependencies using Tarjan's algorithm."""
    from coderecon.index._internal.analysis.code_graph import (
        analyze_def_graph,
        analyze_file_graph,
    )
    from coderecon.mcp.delivery import wrap_response

    engine = app_ctx.coordinator.db.engine
    result = analyze_def_graph(engine) if level == "def" else analyze_file_graph(engine)

    cycles = [{"nodes": sorted(c.nodes), "size": c.size} for c in result.cycles]
    summary = (
        f"{len(cycles)} circular dependency cluster(s) detected at {level} level."
        if cycles
        else f"No circular dependencies at {level} level."
    )
    return wrap_response(
        {"level": level, "cycles": cycles, "summary": summary},
        resource_kind="graph_cycles",
    )

def graph_communities_core(
    app_ctx: AppContext,
    *,
    level: str = "file",
    resolution: float = 1.0,
) -> dict[str, Any]:
    """Detect module communities using Louvain algorithm."""
    from coderecon.index._internal.analysis.code_graph import (
        build_def_graph,
        build_file_graph,
        detect_communities,
    )
    from coderecon.mcp.delivery import wrap_response

    engine = app_ctx.coordinator.db.engine
    g = build_def_graph(engine) if level == "def" else build_file_graph(engine)
    communities = detect_communities(g, resolution=resolution)

    formatted = [
        {
            "id": c.community_id,
            "size": c.size,
            "representative": c.representative,
            "members": c.members[:20],
            "truncated": c.size > 20,
        }
        for c in communities
    ]
    summary = f"{len(communities)} community/ies detected at {level} level."
    return wrap_response(
        {"level": level, "communities": formatted, "summary": summary},
        resource_kind="graph_communities",
    )

def _sync_analysis_sections(engine: Engine) -> tuple[dict[str, Any], GraphAnalysisResult | None]:
    """Run sync graph analysis and return (sections, file_analysis).

    Separated from the async recon_understand_core so that sync DB queries
    are not misleadingly wrapped in an async function.
    """
    sections: dict[str, Any] = {}
    file_analysis = None

    # PageRank analysis
    try:
        from coderecon.index._internal.analysis.code_graph import (
            analyze_def_graph,
            analyze_file_graph,
        )

        def_analysis = analyze_def_graph(engine, top_k=15)
        file_analysis = analyze_file_graph(engine)

        if def_analysis.pagerank:
            sections["top_symbols"] = [
                {
                    "name": s.name,
                    "qualified_name": s.qualified_name,
                    "kind": s.kind,
                    "file": s.file_path,
                    "pagerank": round(s.pagerank, 6),
                }
                for s in def_analysis.pagerank[:15]
            ]
        if file_analysis.pagerank:
            sections["top_files"] = [
                {"file": s.file_path, "pagerank": round(s.pagerank, 6)}
                for s in file_analysis.pagerank[:10]
            ]
    except (ImportError, OSError, ValueError):  # pagerank analysis
        log.warning("understand.pagerank.failed", exc_info=True)

    # Community clusters
    try:
        if file_analysis and file_analysis.communities:
            sections["communities"] = [
                {
                    "id": c.community_id,
                    "size": c.size,
                    "representative": c.representative,
                    "sample_members": c.members[:5],
                }
                for c in file_analysis.communities[:10]
            ]
    except (NameError, AttributeError, TypeError):  # community extraction
        log.debug("understand.communities.failed", exc_info=True)

    # Cycles
    try:
        if file_analysis and file_analysis.cycles:
            sections["cycles"] = [
                {"size": c.size, "nodes": sorted(c.nodes)[:10]}
                for c in file_analysis.cycles[:5]
            ]
    except (NameError, AttributeError, TypeError):  # cycle extraction
        log.debug("understand.cycles.failed", exc_info=True)

    # Coverage
    try:
        from coderecon.index._internal.analysis.coverage_ingestion import get_coverage_summary

        cov = get_coverage_summary(engine)
        if cov.get("total_defs", 0) > 0:
            sections["coverage"] = cov
    except (ImportError, OSError, ValueError):  # coverage summary
        log.warning("understand.coverage.failed", exc_info=True)

    # Lint health
    try:
        from coderecon.index._internal.analysis.lint_status import get_lint_summary

        lint = get_lint_summary(engine)
        if lint.get("files_checked", 0) > 0:
            sections["lint"] = lint
    except (ImportError, OSError, ValueError):  # lint summary
        log.warning("understand.lint.failed", exc_info=True)

    return sections, file_analysis

async def recon_understand_core(
    app_ctx: AppContext,
) -> dict[str, Any]:
    """Full codebase narrative briefing (transport-agnostic)."""
    engine = app_ctx.coordinator.db.engine

    # Section 1: Repo structure (async — uses coordinator.map_repo)
    sections: dict[str, Any] = {}
    try:
        map_result = await app_ctx.coordinator.map_repo(
            include=["structure", "languages", "entry_points"],
            depth=3, limit=100,
        )
        from coderecon.mcp.tools.index import _build_overview

        sections["structure"] = _build_overview(map_result)
    except (ImportError, OSError, ValueError, AttributeError):  # map_repo analysis
        log.warning("understand.structure.failed", exc_info=True)

    # Sections 2-6: Sync graph analysis
    analysis_sections, _file_analysis = _sync_analysis_sections(engine)
    sections.update(analysis_sections)

    # Build summary text
    parts: list[str] = []
    if "top_symbols" in sections:
        top = sections["top_symbols"][0]
        parts.append(
            f"Most central symbol: {top['qualified_name'] or top['name']} "
            f"({top['kind']} in {top['file']})"
        )
    if "communities" in sections:
        parts.append(f"{len(sections['communities'])} module communities detected")
    if "cycles" in sections:
        parts.append(f"{len(sections['cycles'])} circular dependency cluster(s)")
    if "coverage" in sections:
        c = sections["coverage"]
        parts.append(
            f"Coverage: {c['defs_covered']}/{c['total_defs']} defs ({c['coverage_rate']:.0%})"
        )
    if "lint" in sections:
        lint = sections["lint"]
        parts.append(
            f"Lint: {lint['clean_files']}/{lint['files_checked']} files clean ({lint['total_errors']} errors)"
        )

    summary = ". ".join(parts) + "." if parts else "No analysis data available yet."

    from coderecon.mcp.delivery import wrap_response

    return wrap_response(
        {"sections": sections, "summary": summary},
        resource_kind="understand",
    )

def graph_export_core(
    app_ctx: AppContext,
    *,
    output_path: str = "",
    resolution: float = 1.0,
) -> dict[str, Any]:
    """Export interactive dependency graph as HTML (transport-agnostic)."""
    from pathlib import Path

    from coderecon.index._internal.analysis.graph_export import export_graph_html
    from coderecon.mcp.delivery import wrap_response

    engine = app_ctx.coordinator.db.engine
    path = Path(output_path) if output_path else app_ctx.repo_root / ".recon" / "graph.html"

    result_path = export_graph_html(
        engine=engine, output_path=path, level="file", resolution=resolution,
    )
    return wrap_response(
        {"path": str(result_path), "message": f"Graph exported to {result_path}"},
        resource_kind="graph_export",
    )

# Tool Registration

def register_tools(mcp: FastMCP, app_ctx: AppContext) -> None:
    """Register graph analysis MCP tools."""

    @mcp.tool(
        annotations={
            "title": "Graph: detect circular dependencies",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def graph_cycles(
        ctx: Context,
        level: str = Field(
            default="file",
            description="Graph level: 'file' (import cycles) or 'def' (call cycles).",
        ),
    ) -> dict[str, Any]:
        """Detect circular dependencies using Tarjan's algorithm."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)
        return graph_cycles_core(app_ctx, level=level)

    @mcp.tool(
        annotations={
            "title": "Graph: detect module communities",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def graph_communities(
        ctx: Context,
        level: str = Field(
            default="file",
            description="Graph level: 'file' or 'def'.",
        ),
        resolution: float = Field(
            default=1.0,
            description="Louvain resolution parameter. Higher = more communities.",
        ),
    ) -> dict[str, Any]:
        """Detect module communities using Louvain algorithm."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)
        return graph_communities_core(app_ctx, level=level, resolution=resolution)

    @mcp.tool(
        annotations={
            "title": "Understand: full codebase briefing",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def recon_understand(
        ctx: Context,
    ) -> dict[str, Any]:
        """Full codebase narrative briefing."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)
        return await recon_understand_core(app_ctx)

    @mcp.tool(
        annotations={
            "title": "Graph: export interactive HTML visualization",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def graph_export(
        ctx: Context,
        output_path: str = Field(
            default="",
            description="Output path for the HTML file. Defaults to .recon/graph.html.",
        ),
        resolution: float = Field(
            default=1.0,
            description="Louvain community resolution (higher = more communities).",
        ),
    ) -> dict[str, Any]:
        """Export an interactive dependency graph as a self-contained HTML file."""
        return graph_export_core(app_ctx, output_path=output_path, resolution=resolution)
