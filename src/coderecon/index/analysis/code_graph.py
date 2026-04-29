"""Code graph construction and algorithms.

Builds a directed graph from ImportFact (resolved_path) and RefFact→DefFact
edges, then exposes PageRank, Tarjan SCC, and Louvain community detection.

All algorithms are pure functions: graph in → data out.  No mutations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import networkx as nx
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

# Data models

@dataclass(frozen=True, slots=True)
class RankedSymbol:
    """Symbol with PageRank score."""

    def_uid: str
    name: str
    qualified_name: str | None
    kind: str
    file_path: str
    pagerank: float

@dataclass(frozen=True, slots=True)
class CycleCluster:
    """Strongly connected component (circular dependency)."""

    nodes: frozenset[str]  # file paths or def_uids
    size: int

@dataclass(frozen=True, slots=True)
class Community:
    """Louvain community."""

    community_id: int
    members: list[str]  # file paths
    size: int
    representative: str | None  # Highest-PageRank member

@dataclass(slots=True)
class GraphAnalysisResult:
    """Complete analysis result."""

    node_count: int = 0
    edge_count: int = 0
    pagerank: list[RankedSymbol] = field(default_factory=list)
    cycles: list[CycleCluster] = field(default_factory=list)
    communities: list[Community] = field(default_factory=list)

# Graph construction

def build_file_graph(engine: Engine) -> nx.DiGraph:
    """Build directed file-level graph from ImportFact.resolved_path.

    An edge A→B means file A imports something from file B.
    """
    g = nx.DiGraph()
    with engine.connect() as conn:
        # Get all files
        rows = conn.execute(text("SELECT path FROM files")).fetchall()
        for (path,) in rows:
            g.add_node(path)

        # Add import edges: importer → imported (via resolved_path)
        rows = conn.execute(
            text(
                "SELECT DISTINCT f.path, i.resolved_path "
                "FROM import_facts i "
                "JOIN files f ON f.id = i.file_id "
                "WHERE i.resolved_path IS NOT NULL "
                "AND f.path != i.resolved_path"
            )
        ).fetchall()
        for src_path, dst_path in rows:
            g.add_edge(src_path, dst_path)

    return g

def build_def_graph(engine: Engine) -> nx.DiGraph:
    """Build directed definition-level graph from RefFact→DefFact bindings.

    An edge A→B means definition A references (calls/uses) definition B.
    Only includes resolved references (target_def_uid IS NOT NULL).
    """
    g = nx.DiGraph()
    with engine.connect() as conn:
        # Get all defs with file path
        rows = conn.execute(
            text(
                "SELECT d.def_uid, d.name, d.qualified_name, d.kind, f.path "
                "FROM def_facts d "
                "JOIN files f ON f.id = d.file_id"
            )
        ).fetchall()
        for def_uid, name, qname, kind, path in rows:
            g.add_node(
                def_uid,
                name=name,
                qualified_name=qname,
                kind=kind,
                file_path=path,
            )

        # Add reference edges: source_def → target_def
        # A ref belongs to the innermost def that contains it (by file + line range)
        rows = conn.execute(
            text(
                "SELECT DISTINCT r.target_def_uid, sd.def_uid AS source_def_uid "
                "FROM ref_facts r "
                "JOIN def_facts sd ON sd.file_id = r.file_id "
                "  AND r.start_line >= sd.start_line "
                "  AND r.start_line <= sd.end_line "
                "WHERE r.target_def_uid IS NOT NULL "
                "AND r.target_def_uid != sd.def_uid "
                "ORDER BY sd.end_line - sd.start_line ASC"
            )
        ).fetchall()
        seen: set[tuple[str, str]] = set()
        for target_uid, source_uid in rows:
            key = (source_uid, target_uid)
            if key not in seen:
                seen.add(key)
                g.add_edge(source_uid, target_uid)

    return g

# Algorithms

def compute_pagerank(
    g: nx.DiGraph, *, top_k: int = 50
) -> list[RankedSymbol]:
    """Compute PageRank on def graph.  Returns top-K ranked symbols."""
    if g.number_of_nodes() == 0:
        return []

    scores = nx.pagerank(g, alpha=0.85, max_iter=100, tol=1e-06)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    result = []
    for node_id, score in ranked:
        data = g.nodes.get(node_id, {})
        result.append(
            RankedSymbol(
                def_uid=node_id,
                name=data.get("name", node_id),
                qualified_name=data.get("qualified_name"),
                kind=data.get("kind", "unknown"),
                file_path=data.get("file_path", ""),
                pagerank=score,
            )
        )
    return result

def compute_file_pagerank(
    g: nx.DiGraph, *, top_k: int = 30
) -> list[tuple[str, float]]:
    """Compute PageRank on file graph.  Returns top-K (path, score) pairs."""
    if g.number_of_nodes() == 0:
        return []
    scores = nx.pagerank(g, alpha=0.85, max_iter=100, tol=1e-06)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

def detect_cycles(g: nx.DiGraph) -> list[CycleCluster]:
    """Find all strongly connected components with size > 1 (circular deps)."""
    cycles = []
    for component in nx.strongly_connected_components(g):
        if len(component) > 1:
            cycles.append(
                CycleCluster(
                    nodes=frozenset(component),
                    size=len(component),
                )
            )
    # Sort by size descending
    cycles.sort(key=lambda c: c.size, reverse=True)
    return cycles

def detect_communities(
    g: nx.DiGraph, *, resolution: float = 1.0
) -> list[Community]:
    """Detect communities using Louvain on the undirected projection.

    Returns communities sorted by size descending, each with the
    highest-PageRank member as representative.
    """
    if g.number_of_nodes() == 0:
        return []

    undirected = g.to_undirected()

    # Remove isolates for cleaner communities
    isolates = list(nx.isolates(undirected))
    undirected.remove_nodes_from(isolates)

    if undirected.number_of_nodes() == 0:
        return []

    partition = nx.community.louvain_communities(
        undirected, resolution=resolution, seed=42
    )

    # Compute PageRank for representative selection
    pr = nx.pagerank(g, alpha=0.85, max_iter=100, tol=1e-06)

    communities = []
    for i, members in enumerate(partition):
        member_list = sorted(members)
        # Pick highest-PR member as representative
        rep = max(member_list, key=lambda m: pr.get(m, 0.0))
        communities.append(
            Community(
                community_id=i,
                members=member_list,
                size=len(member_list),
                representative=rep,
            )
        )

    communities.sort(key=lambda c: c.size, reverse=True)
    return communities

# Full analysis (convenience)

def analyze_file_graph(engine: Engine) -> GraphAnalysisResult:
    """Build file graph and run all algorithms."""
    g = build_file_graph(engine)
    pr = compute_file_pagerank(g, top_k=30)
    cycles = detect_cycles(g)
    communities = detect_communities(g)

    return GraphAnalysisResult(
        node_count=g.number_of_nodes(),
        edge_count=g.number_of_edges(),
        pagerank=[
            RankedSymbol(
                def_uid=path,
                name=path.rsplit("/", 1)[-1],
                qualified_name=path,
                kind="file",
                file_path=path,
                pagerank=score,
            )
            for path, score in pr
        ],
        cycles=cycles,
        communities=communities,
    )

def analyze_def_graph(engine: Engine, *, top_k: int = 50) -> GraphAnalysisResult:
    """Build def graph and run all algorithms."""
    g = build_def_graph(engine)
    pagerank = compute_pagerank(g, top_k=top_k)
    cycles = detect_cycles(g)
    communities = detect_communities(g)

    return GraphAnalysisResult(
        node_count=g.number_of_nodes(),
        edge_count=g.number_of_edges(),
        pagerank=pagerank,
        cycles=cycles,
        communities=communities,
    )
