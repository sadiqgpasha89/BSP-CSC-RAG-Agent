"""GraphRAG Subsystem for Network Topology & SOP Traversal."""

import re
from typing import Dict, List, Any, Optional, Tuple
from src.data_engineering.graph_rag_builder import graph_builder
from src.data_engineering.ingestion_pipeline import KnowledgeChunk
from src.core.logging import get_logger

logger = get_logger(__name__)


class GraphStoreRetriever:
    """Extracts relational topological insights from NetworkX Knowledge Graph as contextual chunks."""

    def __init__(self):
        self.builder = graph_builder

    @staticmethod
    def _mentions(query_lower: str, alias: str) -> bool:
        """Whole-token match so 'TOWER-10' cannot fire on 'TOWER-104' and short ids never match inside words."""
        return re.search(rf"(?<![a-z0-9_\-]){re.escape(alias)}(?![a-z0-9_\-])", query_lower) is not None

    def search_relational_context(
        self,
        query: str,
        top_k: int = 5,
        metadata_filters: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[KnowledgeChunk, float]]:
        """Identify entities in query, traverse neighborhood, and synthesize graph context chunks.

        ``metadata_filters`` applies the same temporal/contractual isolation as the dense and sparse tiers; tariff
        nodes are tagged with the governing plan so GraphRAG cannot leak 2026 entitlements to a 2018 subscriber.
        """
        results: List[Tuple[KnowledgeChunk, float]] = []
        query_lower = query.lower()

        # Match full ids ("TOWER-104") and un-prefixed aliases ("0x4F" for "ERR_CODE:0x4F", "5G_ULTRA_MAX_2026" for "PLAN:...").
        matched_nodes = []
        for node_id in self.builder.graph.nodes:
            aliases = {node_id.lower(), node_id.split(":", 1)[-1].lower()}
            if any(self._mentions(query_lower, a) for a in aliases):
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

            owner_plan = self.builder.tariff_owner(node_id)
            owner_is_legacy = bool(
                owner_plan and self.builder.graph.nodes.get(f"PLAN:{owner_plan}", {}).get("is_legacy", False)
            )

            chunk = KnowledgeChunk(
                chunk_id=f"GRAPH-{node_id}",
                doc_id=f"GRAPH-{node_id}",
                title=f"GraphRAG: {node_data.get('name', node_id)}",
                category="graph_topology",
                content=content,
                raw_metadata=node_data,
                plan_code=owner_plan,
                is_legacy=owner_is_legacy,
                keywords=[node_id, str(node_data.get("type"))],
            )
            if chunk.matches(metadata_filters):
                results.append((chunk, 0.92))

        return results[:top_k]


graph_retriever = GraphStoreRetriever()
