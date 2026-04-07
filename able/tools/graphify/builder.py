"""
Graphify Knowledge Graph Builder — Visual research topology.

Builds a NetworkX graph from research findings, applies community detection,
and exports to multiple formats: interactive HTML, mermaid diagrams (for Trilium),
and JSON graph data (for semantic search index).

Inspired by safishamsi/graphify's approach to knowledge graph construction.
"""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    import networkx as nx
    NX_AVAILABLE = True
except ImportError:
    NX_AVAILABLE = False


@dataclass
class GraphNode:
    """A node in the research knowledge graph."""
    id: str
    label: str
    node_type: str  # "finding", "topic", "tag", "source", "action"
    weight: float = 1.0
    community: int = -1
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """An edge in the research knowledge graph."""
    source: str
    target: str
    edge_type: str  # "tagged_with", "from_source", "related_to", "action_for"
    weight: float = 1.0


@dataclass
class GraphExport:
    """Exported graph data."""
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    communities: Dict[int, List[str]]  # community_id → [node_ids]
    stats: Dict[str, Any] = field(default_factory=dict)


class ResearchGraphBuilder:
    """
    Build knowledge graphs from research findings.

    Usage:
        builder = ResearchGraphBuilder()
        builder.add_findings(findings_list)
        export = builder.build()
        mermaid = builder.to_mermaid(export)
        html = builder.to_html(export, output_path)
    """

    def __init__(self):
        if not NX_AVAILABLE:
            raise ImportError("networkx required: pip install networkx")
        self.graph = nx.Graph()
        self._findings: List[Dict[str, Any]] = []

    def add_findings(self, findings: List[Dict[str, Any]]):
        """Add research findings to the graph."""
        for f in findings:
            self._add_finding(f)
        self._findings.extend(findings)

    def _add_finding(self, finding: Dict[str, Any]):
        """Add a single finding and its relationships to the graph."""
        title = finding.get("title", "?")[:60]
        fid = f"f:{_safe_id(title)}"

        # Finding node
        self.graph.add_node(fid, label=title, node_type="finding",
                           relevance=finding.get("relevance", "medium"))

        # Tag edges
        for tag in finding.get("tags", []):
            tid = f"t:{_safe_id(tag)}"
            if not self.graph.has_node(tid):
                self.graph.add_node(tid, label=tag, node_type="tag")
            self.graph.add_edge(fid, tid, edge_type="tagged_with", weight=1.0)

        # Source edge
        source = finding.get("source", "")
        if source:
            sid = f"s:{_safe_id(source)}"
            if not self.graph.has_node(sid):
                self.graph.add_node(sid, label=source, node_type="source")
            self.graph.add_edge(fid, sid, edge_type="from_source", weight=0.5)

        # Action edge
        action = finding.get("action", "")
        if action and action != "Review for potential improvement":
            aid = f"a:{_safe_id(action[:40])}"
            if not self.graph.has_node(aid):
                self.graph.add_node(aid, label=action[:60], node_type="action")
            self.graph.add_edge(fid, aid, edge_type="action_for", weight=0.8)

    def build(self) -> GraphExport:
        """Build the graph export with community detection."""
        if self.graph.number_of_nodes() == 0:
            return GraphExport(nodes=[], edges=[], communities={}, stats={})

        # Community detection (Louvain — Leiden requires external lib)
        try:
            from networkx.algorithms.community import louvain_communities
            communities_sets = louvain_communities(self.graph, seed=42)
            community_map = {}
            for idx, comm in enumerate(communities_sets):
                for node in comm:
                    community_map[node] = idx
        except Exception:
            community_map = {n: 0 for n in self.graph.nodes}

        # Build export
        nodes = []
        for nid, data in self.graph.nodes(data=True):
            nodes.append(GraphNode(
                id=nid,
                label=data.get("label", nid),
                node_type=data.get("node_type", "unknown"),
                weight=self.graph.degree(nid),
                community=community_map.get(nid, -1),
                attributes={k: v for k, v in data.items() if k not in ("label", "node_type")},
            ))

        edges = []
        for u, v, data in self.graph.edges(data=True):
            edges.append(GraphEdge(
                source=u, target=v,
                edge_type=data.get("edge_type", "related_to"),
                weight=data.get("weight", 1.0),
            ))

        communities: Dict[int, List[str]] = defaultdict(list)
        for nid, comm in community_map.items():
            communities[comm].append(nid)

        stats = {
            "node_count": self.graph.number_of_nodes(),
            "edge_count": self.graph.number_of_edges(),
            "community_count": len(communities),
            "density": nx.density(self.graph),
            "finding_count": sum(1 for n in nodes if n.node_type == "finding"),
            "tag_count": sum(1 for n in nodes if n.node_type == "tag"),
        }

        return GraphExport(nodes=nodes, edges=edges, communities=dict(communities), stats=stats)

    def to_mermaid(self, export: GraphExport) -> str:
        """Convert graph to mermaid diagram for Trilium."""
        if not export.nodes:
            return ""

        lines = ["graph TD"]

        # Color-code by node type
        type_shapes = {
            "finding": ('["', '"]'),
            "tag": ('(("', '"))'),
            "source": ('{"', '"}'),
            "action": ('["', '"]'),
        }

        for node in export.nodes:
            l, r = type_shapes.get(node.node_type, ('["', '"]'))
            safe_label = node.label.replace('"', "'").replace("\n", " ")
            lines.append(f'    {node.id.replace(":", "_")}{l}{safe_label}{r}')

        for edge in export.edges:
            src = edge.source.replace(":", "_")
            tgt = edge.target.replace(":", "_")
            label = edge.edge_type.replace("_", " ")
            lines.append(f'    {src} -->|{label}| {tgt}')

        # Style by node type
        finding_ids = " ".join(n.id.replace(":", "_") for n in export.nodes if n.node_type == "finding")
        tag_ids = " ".join(n.id.replace(":", "_") for n in export.nodes if n.node_type == "tag")
        if finding_ids:
            lines.append(f"    style {finding_ids.split()[0]} fill:#4a9eff")
        if tag_ids:
            lines.append(f"    style {tag_ids.split()[0]} fill:#ff9f43")

        return "\n".join(lines)

    def to_json(self, export: GraphExport) -> str:
        """Export graph as JSON for indexing."""
        return json.dumps({
            "nodes": [
                {"id": n.id, "label": n.label, "type": n.node_type,
                 "weight": n.weight, "community": n.community}
                for n in export.nodes
            ],
            "edges": [
                {"source": e.source, "target": e.target,
                 "type": e.edge_type, "weight": e.weight}
                for e in export.edges
            ],
            "communities": export.communities,
            "stats": export.stats,
        }, indent=2)

    def to_html(self, export: GraphExport, output_path: Path) -> Path:
        """Export interactive HTML visualization using D3.js (via CDN)."""
        graph_json = self.to_json(export)
        html = f"""<!DOCTYPE html>
<html><head><title>ABLE Research Graph</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
body {{ margin: 0; background: #1a1a2e; color: #eee; font-family: sans-serif; }}
svg {{ width: 100vw; height: 100vh; }}
.node {{ cursor: pointer; }}
.link {{ stroke: #555; stroke-opacity: 0.6; }}
text {{ font-size: 10px; fill: #ccc; }}
</style></head><body>
<svg></svg>
<script>
const data = {graph_json};
const svg = d3.select("svg");
const width = window.innerWidth, height = window.innerHeight;
const color = d3.scaleOrdinal(d3.schemeCategory10);

const sim = d3.forceSimulation(data.nodes)
    .force("link", d3.forceLink(data.edges).id(d => d.id).distance(80))
    .force("charge", d3.forceManyBody().strength(-200))
    .force("center", d3.forceCenter(width/2, height/2));

const link = svg.selectAll(".link").data(data.edges).join("line").attr("class","link");
const node = svg.selectAll(".node").data(data.nodes).join("g").attr("class","node")
    .call(d3.drag().on("start",ds).on("drag",dd).on("end",de));
node.append("circle").attr("r", d => 4 + d.weight).attr("fill", d => color(d.community));
node.append("text").text(d => d.label).attr("dx",12).attr("dy",4);

sim.on("tick", () => {{
    link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y)
        .attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
    node.attr("transform",d=>`translate(${{d.x}},${{d.y}})`);
}});

function ds(e,d){{ if(!e.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }}
function dd(e,d){{ d.fx=e.x; d.fy=e.y; }}
function de(e,d){{ if(!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }}
</script></body></html>"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html)
        return output_path


def _safe_id(text: str) -> str:
    """Convert text to a safe graph node ID."""
    import re
    return re.sub(r"[^a-zA-Z0-9_]", "_", text.strip().lower())[:40]


async def build_research_graph(
    findings: List[Dict[str, Any]],
    output_dir: Path = None,
) -> Optional[GraphExport]:
    """
    Build a knowledge graph from research findings and export artifacts.

    Returns the GraphExport and saves:
    - data/research_graph.html (interactive D3 visualization)
    - data/research_graph.json (for semantic search indexing)
    """
    if not NX_AVAILABLE:
        logger.warning("networkx not installed — skipping graph build")
        return None

    if output_dir is None:
        output_dir = Path(__file__).parent.parent.parent.parent / "data"

    builder = ResearchGraphBuilder()
    builder.add_findings(findings)
    export = builder.build()

    if not export.nodes:
        return export

    # Save HTML visualization
    html_path = builder.to_html(export, output_dir / "research_graph.html")
    logger.info("Research graph HTML: %s (%d nodes, %d edges)",
                html_path, export.stats["node_count"], export.stats["edge_count"])

    # Save JSON for indexing
    json_path = output_dir / "research_graph.json"
    json_path.write_text(builder.to_json(export))

    return export
