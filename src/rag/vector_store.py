"""Dense Vector Store with Approximate Nearest Neighbor (ANN) Cosine Similarity."""

import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from sklearn.feature_extraction.text import TfidfVectorizer
from src.data_engineering.ingestion_pipeline import KnowledgeChunk, ingestion_pipeline
from src.core.logging import get_logger

logger = get_logger(__name__)


class DenseVectorStore:
    """In-memory Dense Vector Index emulating OpenSearch / Milvus HNSW dense embedding index."""

    def __init__(self):
        self.chunks: List[KnowledgeChunk] = []
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=1024, stop_words="english")
        self.embeddings: Optional[np.ndarray] = None
        self._is_indexed: bool = False
        self.initialize_index()

    def initialize_index(self) -> None:
        """Load chunks from ingestion pipeline and build the vector space representation."""
        self.chunks = ingestion_pipeline.load_and_chunk_all()
        if not self.chunks:
            logger.warning("No chunks available to index in dense vector store")
            return

        corpus = [f"{c.title} {c.category} {c.content} {' '.join(c.keywords)}" for c in self.chunks]
        tfidf_matrix = self.vectorizer.fit_transform(corpus).toarray()

        # L2-normalize vectors for cosine similarity via dot product
        norms = np.linalg.norm(tfidf_matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.embeddings = tfidf_matrix / norms
        self._is_indexed = True
        logger.info("Dense vector index initialized", indexed_chunks=len(self.chunks))

    def search(
        self,
        query: str,
        top_k: int = 10,
        metadata_filters: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[KnowledgeChunk, float]]:
        """Perform cosine similarity search against indexed chunks with optional metadata filtering."""
        if not self._is_indexed or self.embeddings is None:
            return []

        query_vec = self.vectorizer.transform([query]).toarray()
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        scores = np.dot(self.embeddings, query_vec.T).flatten()

        # Metadata pre-filtering happens BEFORE ranking; zero-similarity chunks are not "matches" and are dropped so
        # they cannot receive spurious Reciprocal Rank Fusion credit downstream.
        ranked = sorted(
            (
                (chunk, float(scores[idx]))
                for idx, chunk in enumerate(self.chunks)
                if scores[idx] > 0.0 and chunk.matches(metadata_filters)
            ),
            key=lambda item: item[1],
            reverse=True,
        )

        return ranked[:top_k]


vector_store = DenseVectorStore()
