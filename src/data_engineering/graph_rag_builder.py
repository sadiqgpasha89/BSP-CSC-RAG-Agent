"""GraphRAG Knowledge Graph Builder & Network Topology Graph Constructor."""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional
import networkx as nx
from src.core.logging import get_logger

logger = get_logger(__name__)


class GraphRAGBuilder:
    """Builds and queries the enterprise Network Topology and SOP Knowledge Graph."""

    def __init__(self, topology_path: str = "data/network_topology.json"):
        self.topology_path = Path(topology_path)
        self.graph = nx.DiGraph()
        self._build_graph()

    def _build_graph(self) -> None:
        """Populate NetworkX directed graph from topology dataset and diagnostic relationships."""
        if not self.topology_path.exists():
            logger.warning("Topology path not found, initializing empty graph", path=str(self.topology_path))
            return

        with open(self.topology_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Add Nodes
        for node in data.get("nodes", []):
            self.graph.add_node(
                node["id"],
                type=node.get("type"),
                name=node.get("name"),
                sector=node.get("sector"),
                status=node.get("status", "operational"),
                attributes=node,
            )

        # Add Edges
        for edge in data.get("edges", []):
            self.graph.add_edge(
                edge["source"],
                edge["target"],
                relation=edge.get("relation"),
                latency_ms=edge.get("latency_ms", 1.0),
            )
            # Bidirectional connection representation for network links
            self.graph.add_edge(
                edge["target"],
                edge["source"],
                relation=f"reverse_{edge.get('relation')}",
                latency_ms=edge.get("latency_ms", 1.0),
            )

        # Add Diagnostic & Tariff Knowledge Graph Subgraphs
        self._add_tariff_and_diagnostic_relations()
        logger.info("Knowledge Graph initialized", total_nodes=self.graph.number_of_nodes(), total_edges=self.graph.number_of_edges())

    def _add_tariff_and_diagnostic_relations(self) -> None:
        """Inject domain relationships between plans, error codes, and resolution tools."""
        # Nodes
        self.graph.add_node("PLAN:5G_ULTRA_MAX_2026", type="tariff_plan", name="5G Ultra Max Unlimited 2026")
        self.graph.add_node("PLAN:FREEDOM_UNLIMITED_2018", type="tariff_plan", name="Legacy Freedom Unlimited (2018)", is_legacy=True)
        self.graph.add_node("FEATURE:50GB_HOTSPOT", type="plan_feature", name="50GB High Speed Hotspot")
        self.graph.add_node("FEATURE:10GB_HOTSPOT", type="plan_feature", name="Strict 10GB Legacy Hotspot")
        self.graph.add_node("ERR_CODE:0x4F", type="error_code", name="ERR_NW_ATTACH_TIMEOUT_0x4F")
        self.graph.add_node("TOOL:OSS_RESET_PDP", type="remediation_tool", name="OSS Core Soft Reset")
        self.graph.add_node("TOOL:OTDR_LINE_TEST", type="remediation_tool", name="Optical OTDR Test")

        # Relations
        self.graph.add_edge("PLAN:5G_ULTRA_MAX_2026", "FEATURE:50GB_HOTSPOT", relation="entitles_feature")
        self.graph.add_edge("PLAN:FREEDOM_UNLIMITED_2018", "FEATURE:10GB_HOTSPOT", relation="entitles_feature")
        self.graph.add_edge("ERR_CODE:0x4F", "TOOL:OSS_RESET_PDP", relation="remediated_by")
        self.graph.add_edge("TOWER-104", "ERR_CODE:0x4F", relation="triggers_error_during_cut")
        self.graph.add_edge("OLT-OPTICAL-01", "TOOL:OTDR_LINE_TEST", relation="requires_line_diagnostic")

    def query_subgraph(self, entity_id: str, depth: int = 2) -> Dict[str, Any]:
        """Perform multi-hop traversal around an entity or tower to retrieve connected topological context."""
        if entity_id not in self.graph:
            return {"error": f"Entity '{entity_id}' not found in Knowledge Graph", "nodes": [], "edges": []}

        subgraph_nodes = set([entity_id])
        current_layer = set([entity_id])

        for _ in range(depth):
            next_layer = set()
            for node in current_layer:
                neighbors = list(self.graph.neighbors(node))
                next_layer.update(neighbors)
            subgraph_nodes.update(next_layer)
            current_layer = next_layer

        sub = self.graph.subgraph(subgraph_nodes)

        nodes_data = [{"id": n, "data": sub.nodes[n]} for n in sub.nodes()]
        edges_data = [{"source": u, "target": v, "data": sub.get_edge_data(u, v)} for u, v in sub.edges()]

        return {
            "root_entity": entity_id,
            "total_nodes": len(nodes_data),
            "total_edges": len(edges_data),
            "nodes": nodes_data,
            "edges": edges_data,
        }

    def find_shortest_path(self, source: str, target: str) -> Optional[List[str]]:
        """Find deterministic multi-hop path between network infrastructure or entities."""
        try:
            return nx.shortest_path(self.graph, source=source, target=target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None


graph_builder = GraphRAGBuilder()
