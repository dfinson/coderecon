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

    from coderecon.mcp.context import AppContext

log = structlog.get_logger(__name__)


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register graph analysis MCP tools."""

    # ------------------------------------------------------------------
    # Cycles (Tarjan SCC)
    # ------------------------------------------------------------------

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
        """Detect circular dependencies using Tarjan's algorithm.

        Returns strongly connected components with size > 1,
        sorted by size descending.
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        from coderecon.index._internal.analysis.code_graph import (
            analyze_def_graph,
            analyze_file_graph,
        )

        engine = app_ctx.coordinator.db.engine

        if level == "def":
            result = analyze_def_graph(engine)
        else:
            result = analyze_file_graph(engine)

        cycles = [
            {"nodes": sorted(c.nodes), "size": c.size}
            for c in result.cycles
        ]

        summary = (
            f"{len(cycles)} circular dependency cluster(s) detected at {level} level."
            if cycles
            else f"No circular dependencies at {level} level."
        )

        from coderecon.mcp.delivery import wrap_response

        return wrap_response(
            {"level": level, "cycles": cycles, "summary": summary},
            resource_kind="graph_cycles",
            session_id=ctx.session_id,
        )

    # ------------------------------------------------------------------
    # Communities (Louvain)
    # ------------------------------------------------------------------

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
        """Detect module communities using Louvain algorithm.

        Returns communities sorted by size, each with a representative
        (highest PageRank member).
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        from coderecon.index._internal.analysis.code_graph import (
            build_def_graph,
            build_file_graph,
            detect_communities,
        )

        engine = app_ctx.coordinator.db.engine

        if level == "def":
            g = build_def_graph(engine)
        else:
            g = build_file_graph(engine)

        communities = detect_communities(g, resolution=resolution)

        formatted = [
            {
                "id": c.community_id,
                "size": c.size,
                "representative": c.representative,
                "members": c.members[:20],  # Cap display
                "truncated": c.size > 20,
            }
            for c in communities
        ]

        summary = f"{len(communities)} community/ies detected at {level} level."

        from coderecon.mcp.delivery import wrap_response

        return wrap_response(
            {"level": level, "communities": formatted, "summary": summary},
            resource_kind="graph_communities",
            session_id=ctx.session_id,
        )

    # ------------------------------------------------------------------
    # recon_understand — narrative briefing
    # ------------------------------------------------------------------

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
        """Full codebase narrative briefing.

        Combines: repo map + PageRank top symbols + community clusters +
        coverage summary + lint health + cycle detection.

        Sections are omitted gracefully if data is unavailable.
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        sections: dict[str, Any] = {}
        engine = app_ctx.coordinator.db.engine

        # Section 1: Repo structure (from existing recon_map)
        try:
            map_result = await app_ctx.coordinator.map_repo(
                include=["structure", "languages", "entry_points"],
                depth=3, limit=100,
            )
            from coderecon.mcp.tools.index import _build_overview

            sections["structure"] = _build_overview(map_result)
        except Exception:  # noqa: BLE001
            log.warning("understand.structure.failed", exc_info=True)

        # Section 2: Top symbols by PageRank
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
        except Exception:  # noqa: BLE001
            log.warning("understand.pagerank.failed", exc_info=True)

        # Section 3: Community clusters
        try:
            if file_analysis.communities:
                sections["communities"] = [
                    {
                        "id": c.community_id,
                        "size": c.size,
                        "representative": c.representative,
                        "sample_members": c.members[:5],
                    }
                    for c in file_analysis.communities[:10]
                ]
        except Exception:  # noqa: BLE001
            pass

        # Section 4: Circular dependencies
        try:
            if file_analysis.cycles:
                sections["cycles"] = [
                    {"size": c.size, "nodes": sorted(c.nodes)[:10]}
                    for c in file_analysis.cycles[:5]
                ]
        except Exception:  # noqa: BLE001
            pass

        # Section 5: Coverage summary
        try:
            from coderecon.index._internal.analysis.coverage_ingestion import (
                get_coverage_summary,
            )

            cov = get_coverage_summary(engine)
            if cov.get("total_defs", 0) > 0:
                sections["coverage"] = cov
        except Exception:  # noqa: BLE001
            log.warning("understand.coverage.failed", exc_info=True)

        # Section 6: Lint health
        try:
            from coderecon.index._internal.analysis.lint_status import (
                get_lint_summary,
            )

            lint = get_lint_summary(engine)
            if lint.get("files_checked", 0) > 0:
                sections["lint"] = lint
        except Exception:  # noqa: BLE001
            log.warning("understand.lint.failed", exc_info=True)

        # Build summary text
        parts = []
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
            cov = sections["coverage"]
            parts.append(
                f"Coverage: {cov['defs_covered']}/{cov['total_defs']} defs "
                f"({cov['coverage_rate']:.0%})"
            )
        if "lint" in sections:
            lint = sections["lint"]
            parts.append(
                f"Lint: {lint['clean_files']}/{lint['files_checked']} files clean "
                f"({lint['total_errors']} errors)"
            )

        summary = ". ".join(parts) + "." if parts else "No analysis data available yet."

        from coderecon.mcp.delivery import wrap_response

        return wrap_response(
            {"sections": sections, "summary": summary},
            resource_kind="understand",
            session_id=ctx.session_id,
        )

    # ------------------------------------------------------------------
    # Graph export (interactive HTML)
    # ------------------------------------------------------------------

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
        """Export an interactive dependency graph as a self-contained HTML file.

        Uses vis.js for visualization with:
        - Nodes colored by community (Louvain detection)
        - Node size proportional to PageRank
        - Cycle edges highlighted in red
        - Search, zoom, and hover details
        """
        from pathlib import Path

        from coderecon.index._internal.analysis.graph_export import export_graph_html

        engine = app_ctx.coordinator.db.engine

        if not output_path:
            path = app_ctx.repo_root / ".recon" / "graph.html"
        else:
            path = Path(output_path)

        result_path = export_graph_html(
            engine=engine,
            output_path=path,
            level="file",
            resolution=resolution,
        )

        from coderecon.mcp.delivery import wrap_response

        return wrap_response(
            {
                "path": str(result_path),
                "message": f"Graph exported to {result_path}",
            },
            resource_kind="graph_export",
            session_id=ctx.session_id,
        )
