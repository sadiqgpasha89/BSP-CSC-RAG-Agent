"""Hybrid Enterprise RAG Inspection API Router."""

from typing import List, Optional
from fastapi import APIRouter
from pydantic import BaseModel
from src.rag.hybrid_retriever import hybrid_retriever, RetrievalResult
from src.data_engineering.graph_rag_builder import graph_builder

router = APIRouter(prefix="/rag", tags=["Hybrid RAG & Knowledge Graph"])


class RAGSearchRequest(BaseModel):
    query: str
    subscriber_plan_code: Optional[str] = None
    top_k: int = 5


@router.post("/search", response_model=List[RetrievalResult])
def run_hybrid_search(req: RAGSearchRequest):
    """Execute 4-tier hybrid retrieval (dense + BM25 + GraphRAG + RRF reranker)."""
    return hybrid_retriever.retrieve(
        query=req.query,
        subscriber_plan_code=req.subscriber_plan_code,
        top_k=req.top_k,
    )


@router.get("/subgraph/{entity_id}")
def inspect_subgraph(entity_id: str):
    """Traverse GraphRAG neighborhood around an entity."""
    return graph_builder.query_subgraph(entity_id=entity_id, depth=2)
