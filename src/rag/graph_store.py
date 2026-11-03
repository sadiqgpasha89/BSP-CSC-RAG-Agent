"""GraphRAG Subsystem for Network Topology & SOP Traversal."""

from typing import Dict, List, Any, Optional, Tuple
from src.data_engineering.graph_rag_builder import graph_builder
from src.data_engineering.ingestion_pipeline import KnowledgeChunk
from src.core.logging import get_logger

logger = get_logger(__name__)


class GraphStoreRetriever:
    """Extracts relational topological insights from NetworkX Knowledge Graph as contextual chunks."""

    def __init__(self):
        self.builder = graph_builder

    def search_relational_context(self, query: str, top_k: int = 5) -> List[Tuple[KnowledgeChunk, float]]:
        """Identify entities in query, traverse neighborhood, and synthesize graph context chunks."""
        results: List[Tuple[KnowledgeChunk, float]] = []

        # Check for presence of graph nodes in query
        matched_nodes = []
        for node_id in self.builder.graph.nodes:
            if node_id.lower() in query.lower() or node_id.replace("PLAN:", "").lower() in query.lower():
                matched_nodes.append(node_id)

        for node_id in matched_nodes:
            subgraph_info = self.builder.query_subgraph(node_id, depth=2)
            node_data = self.builder.graph.nodes[node_id]

            # Construct synthetic knowledge chunk from graph traversal
            connections_desc = []
            for edge in subgraph_info.get("edges", []):
                u, v, d = edge["source"], edge["target"], edge["data"]
                rel = d.get("relation", "connected_to")
                connections_desc.append(f"  * [{u}] --({rel})--> [{v}]")

            content = (
                f"GraphRAG Topological Subgraph for Entity: {node_id}\n"
                f"Entity Type: {node_data.get('type')} | Name: {node_data.get('name')}\n"
                f"Relational Graph Dependencies (Depth 2):\n"
                + ("\n".join(connections_desc) if connections_desc else "  No explicit external edges.")
            )

            chunk = KnowledgeChunk(
                chunk_id=f"GRAPH-{node_id}",
                doc_id=f"GRAPH-{node_id}",
                title=f"GraphRAG: {node_data.get('name', node_id)}",
                category="graph_topology",
                content=content,
                raw_metadata=node_data,
                keywords=[node_id, str(node_data.get("type"))],
            )
            results.append((chunk, 0.92))

        return results[:top_k]


graph_retriever = GraphStoreRetriever()
