"""4-Tier Hybrid Enterprise RAG Engine with Reciprocal Rank Fusion (RRF) & Neural Reranking."""

from typing import Dict, List, Any, Optional, Tuple
from pydantic import BaseModel, Field
from src.core.config import settings
from src.data_engineering.ingestion_pipeline import KnowledgeChunk
from src.rag.vector_store import vector_store
from src.rag.bm25_engine import bm25_engine
from src.rag.graph_store import graph_retriever
from src.rag.temporal_filter import temporal_filter_engine
from src.core.logging import get_logger

logger = get_logger(__name__)


class RetrievalResult(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    category: str
    content: str
    rrf_score: float
    rerank_score: float
    retrieval_sources: List[str]
    metadata: Dict[str, Any] = Field(default_factory=dict)


class HybridRetriever:
    """Combines Dense Vectors, BM25 Lexical, GraphRAG, RRF fusion, and Cross-Encoder Reranking."""

    def __init__(self, k_rrf: int = 60):
        self.k_rrf = k_rrf
        # Dynamic weights can be updated via MLOps self-healing loop
        self.dense_weight: float = 1.0
        self.bm25_weight: float = 1.0
        self.graph_weight: float = 1.0

    def set_dynamic_weights(self, dense_w: float, bm25_w: float, graph_w: float) -> None:
        """Dynamically rebalance retrieval source contributions during concept or vocabulary drift."""
        self.dense_weight = dense_w
        self.bm25_weight = bm25_w
        self.graph_weight = graph_w
        logger.info("Hybrid retriever weights rebalanced", dense=dense_w, bm25=bm25_w, graph=graph_w)

    def retrieve(
        self,
        query: str,
        subscriber_plan_code: Optional[str] = None,
        top_k: int = 5,
    ) -> List[RetrievalResult]:
        """Execute 4-tier hybrid retrieval with temporal contract isolation."""
        # 1. Temporal Metadata Filtering
        filters = temporal_filter_engine.build_filters(subscriber_plan_code=subscriber_plan_code, query=query)

        # 2. Parallel Tier Retrievals
        dense_results = vector_store.search(query=query, top_k=15, metadata_filters=filters)
        bm25_results = bm25_engine.search(query=query, top_k=15, metadata_filters=filters)
        graph_results = graph_retriever.search_relational_context(query=query, top_k=5, metadata_filters=filters)

        # 3. Reciprocal Rank Fusion (RRF)
        # RRF(d) = sum( weight_m / (k + rank_m(d)) )
        rrf_scores: Dict[str, float] = {}
        chunk_map: Dict[str, KnowledgeChunk] = {}
        sources_map: Dict[str, List[str]] = {}

        # Dense
        for rank, (chunk, _) in enumerate(dense_results, start=1):
            cid = chunk.chunk_id
            chunk_map[cid] = chunk
            sources_map.setdefault(cid, []).append("dense_vector")
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (self.dense_weight / (self.k_rrf + rank))

        # BM25
        for rank, (chunk, _) in enumerate(bm25_results, start=1):
            cid = chunk.chunk_id
            chunk_map[cid] = chunk
            if "bm25_lexical" not in sources_map.setdefault(cid, []):
                sources_map[cid].append("bm25_lexical")
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (self.bm25_weight / (self.k_rrf + rank))

        # GraphRAG
        for rank, (chunk, _) in enumerate(graph_results, start=1):
            cid = chunk.chunk_id
            chunk_map[cid] = chunk
            if "graph_rag" not in sources_map.setdefault(cid, []):
                sources_map[cid].append("graph_rag")
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (self.graph_weight / (self.k_rrf + rank))

        if not chunk_map:
            logger.warning("No chunks retrieved across any hybrid tier", query=query)
            return []

        # 4. Neural Cross-Encoder Reranker Simulation
        # Combines semantic lexical overlap, title relevance, and RRF score
        reranked: List[RetrievalResult] = []
        query_words = set(query.lower().split())

        for cid, rrf_val in rrf_scores.items():
            chunk = chunk_map[cid]
            content_lower = chunk.content.lower()
            overlap = sum(1 for w in query_words if len(w) > 2 and w in content_lower)
            lexical_boost = min(0.35, overlap * 0.08)

            # Cross-encoder simulated score in [0.0, 1.0]
            neural_rerank_score = min(0.99, (rrf_val * 25.0) + lexical_boost)

            reranked.append(
                RetrievalResult(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    title=chunk.title,
                    category=chunk.category,
                    content=chunk.content,
                    rrf_score=round(rrf_val, 6),
                    rerank_score=round(neural_rerank_score, 4),
                    retrieval_sources=sources_map[cid],
                    metadata=chunk.raw_metadata,
                )
            )

        reranked.sort(key=lambda r: r.rerank_score, reverse=True)
        top_results = reranked[:top_k]

        logger.debug("Hybrid retrieval completed", top_count=len(top_results), top_score=top_results[0].rerank_score if top_results else 0.0)
        return top_results


hybrid_retriever = HybridRetriever()
