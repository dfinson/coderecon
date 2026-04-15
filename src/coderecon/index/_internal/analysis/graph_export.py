"""Interactive dependency graph export — vis.js HTML visualization.

Generates a self-contained HTML file with:
- vis.js network graph (file-level or def-level)
- Color-coded by community (Louvain)
- Node size by PageRank
- Cycle highlighting in red
- Zoom, pan, search, and hover details
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from coderecon.index._internal.analysis.code_graph import (
    build_file_graph,
    compute_file_pagerank,
    detect_communities,
    detect_cycles,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

# Community color palette (vis.js color names)
_COLORS = [
    "#4285f4", "#ea4335", "#fbbc04", "#34a853", "#ff6d01",
    "#46bdc6", "#7baaf7", "#f07b72", "#fcd04f", "#78c257",
    "#ff9e80", "#80deea", "#b39ddb", "#f48fb1", "#a5d6a7",
]


def export_graph_html(
    engine: Engine,
    output_path: Path,
    *,
    level: str = "file",
    resolution: float = 1.0,
) -> Path:
    """Export an interactive HTML dependency graph.

    Args:
        engine: SQLAlchemy engine with indexed DB.
        output_path: Where to write the HTML file.
        level: "file" or "def".
        resolution: Louvain resolution (higher = more communities).

    Returns:
        Path to the generated HTML file.
    """
    g = build_file_graph(engine)
    if g.number_of_nodes() == 0:
        output_path.write_text(_EMPTY_HTML)
        return output_path

    # Compute analysis
    pagerank = dict(compute_file_pagerank(g, top_k=g.number_of_nodes()))
    max_pr = max(pagerank.values()) if pagerank else 1.0

    communities = detect_communities(g, resolution=resolution)
    cycles = detect_cycles(g)
    cycle_nodes = set()
    for c in cycles:
        cycle_nodes.update(c.nodes)

    # Build community membership map
    node_community: dict[str, int] = {}
    for comm in communities:
        for member in comm.members:
            node_community[member] = comm.community_id

    # Build vis.js data
    nodes = []
    for node in g.nodes():
        pr = pagerank.get(node, 0.0)
        size = 10 + (pr / max_pr) * 40 if max_pr > 0 else 10
        comm_id = node_community.get(node, 0)
        color = _COLORS[comm_id % len(_COLORS)]
        border_color = "#ff0000" if node in cycle_nodes else color

        # Short label: just the filename
        label = Path(node).name if "/" in node else node

        nodes.append({
            "id": node,
            "label": label,
            "title": f"{node}\nPageRank: {pr:.6f}\nCommunity: {comm_id}"
                     + ("\n⚠ In dependency cycle" if node in cycle_nodes else ""),
            "size": round(size, 1),
            "color": {
                "background": color,
                "border": border_color,
                "highlight": {"border": border_color, "background": color},
            },
            "borderWidth": 3 if node in cycle_nodes else 1,
        })

    edges = []
    for src, dst in g.edges():
        is_cycle = src in cycle_nodes and dst in cycle_nodes
        edges.append({
            "from": src,
            "to": dst,
            "arrows": "to",
            "color": {"color": "#ff0000" if is_cycle else "#cccccc"},
            "width": 2 if is_cycle else 1,
        })

    html = _TEMPLATE.replace("{{NODES}}", json.dumps(nodes))
    html = html.replace("{{EDGES}}", json.dumps(edges))
    html = html.replace("{{STATS}}", json.dumps({
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "communities": len(communities),
        "cycles": len(cycles),
    }))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return output_path


_EMPTY_HTML = """<!DOCTYPE html>
<html><body><h2>No graph data available</h2>
<p>Index the repository first with <code>recon up</code>.</p>
</body></html>"""

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CodeRecon Dependency Graph</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, system-ui, sans-serif; background: #1a1a2e; color: #e0e0e0; }
  #header { padding: 12px 20px; background: #16213e; display: flex; justify-content: space-between; align-items: center; }
  #header h1 { font-size: 18px; font-weight: 600; }
  #stats { font-size: 13px; opacity: 0.7; }
  #search-box { padding: 6px 12px; border-radius: 4px; border: 1px solid #444; background: #0f3460; color: #e0e0e0; width: 240px; }
  #graph { width: 100%; height: calc(100vh - 50px); }
  .legend { position: fixed; bottom: 12px; left: 12px; background: rgba(22,33,62,0.9); padding: 10px; border-radius: 6px; font-size: 12px; }
  .legend-item { display: flex; align-items: center; gap: 6px; margin: 3px 0; }
  .legend-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
</style>
</head>
<body>
<div id="header">
  <h1>CodeRecon Dependency Graph</h1>
  <span id="stats"></span>
  <input id="search-box" type="text" placeholder="Search files...">
</div>
<div id="graph"></div>
<div class="legend">
  <div class="legend-item"><span class="legend-dot" style="background:#ff0000;border:2px solid #ff0000"></span> Cycle member</div>
  <div class="legend-item"><span class="legend-dot" style="background:#4285f4"></span> Community (colored)</div>
  <div class="legend-item">Node size = PageRank score</div>
</div>
<script>
const nodes = new vis.DataSet({{NODES}});
const edges = new vis.DataSet({{EDGES}});
const stats = {{STATS}};

document.getElementById('stats').textContent =
  `${stats.nodes} files · ${stats.edges} imports · ${stats.communities} communities · ${stats.cycles} cycles`;

const container = document.getElementById('graph');
const network = new vis.Network(container, { nodes, edges }, {
  physics: {
    solver: 'forceAtlas2Based',
    forceAtlas2Based: { gravitationalConstant: -30, centralGravity: 0.005, springLength: 100 },
    stabilization: { iterations: 150 },
  },
  interaction: { hover: true, tooltipDelay: 100, zoomView: true },
  edges: { smooth: { type: 'continuous' } },
});

// Search functionality
document.getElementById('search-box').addEventListener('input', function(e) {
  const q = e.target.value.toLowerCase();
  if (!q) { nodes.forEach(n => nodes.update({ id: n.id, opacity: 1.0 })); return; }
  nodes.forEach(n => {
    const match = n.id.toLowerCase().includes(q) || n.label.toLowerCase().includes(q);
    nodes.update({ id: n.id, opacity: match ? 1.0 : 0.15 });
  });
  const matches = nodes.get().filter(n => n.id.toLowerCase().includes(q));
  if (matches.length === 1) network.focus(matches[0].id, { scale: 1.5, animation: true });
});
</script>
</body>
</html>"""
