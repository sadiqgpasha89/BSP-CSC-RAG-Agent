"""Unit Tests for 4-Tier Hybrid Enterprise RAG."""

from src.rag.vector_store import vector_store
from src.rag.bm25_engine import bm25_engine
from src.rag.graph_store import graph_retriever
from src.rag.hybrid_retriever import hybrid_retriever
from src.rag.temporal_filter import temporal_filter_engine


def test_dense_vector_search():
    query = "unlimited 5G high speed mobile hotspot"
    results = vector_store.search(query=query, top_k=3)
    assert len(results) > 0
    top_chunk, score = results[0]
    assert score > 0.0
    assert "5G" in top_chunk.title or "hotspot" in top_chunk.content.lower()


def test_sparse_bm25_exact_acronym():
    query = "ERR_NW_ATTACH_TIMEOUT_0x4F"
    results = bm25_engine.search(query=query, top_k=3)
    assert len(results) > 0
    top_chunk, score = results[0]
    assert "0x4F" in top_chunk.content or "ERR_NW_ATTACH_TIMEOUT_0x4F" in top_chunk.content


def test_graph_rag_relational_search():
    query = "TOWER-104 router"
    results = graph_retriever.search_relational_context(query=query, top_k=3)
    assert len(results) > 0
    top_chunk, score = results[0]
    assert "GraphRAG" in top_chunk.title
    assert "TOWER-104" in top_chunk.content


def test_hybrid_retrieval_rrf():
    query = "Optical OTDR line test and fiber cut diagnostics"
    results = hybrid_retriever.retrieve(query=query, top_k=3)
    assert len(results) > 0
    top_res = results[0]
    assert top_res.rrf_score > 0.0
    assert "OTDR" in top_res.title or "OTDR" in top_res.content


def test_temporal_filter_legacy_isolation():
    # Grandfathered customer query
    filters = temporal_filter_engine.build_filters(
        subscriber_plan_code="FREEDOM_UNLIMITED_2018",
        query="What is my hotspot limit?",
    )
    assert filters == {"plan_code": "FREEDOM_UNLIMITED_2018"}

    # Modern 2026 customer query excludes legacy
    filters_modern = temporal_filter_engine.build_filters(
        subscriber_plan_code="5G_ULTRA_MAX_2026",
        query="What is my hotspot limit?",
    )
    assert filters_modern == {"is_legacy": False}
