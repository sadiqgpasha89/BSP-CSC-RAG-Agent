"""Sparse Lexical BM25 Engine for Telecom Acronyms & Error Codes."""

import math
import re
from typing import Dict, List, Any, Optional, Tuple
from collections import Counter
from src.data_engineering.ingestion_pipeline import KnowledgeChunk, ingestion_pipeline
from src.core.logging import get_logger

logger = get_logger(__name__)


class BM25Engine:
    """Okapi BM25 ranking algorithm optimized for exact keyword and acronym precision."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.chunks: List[KnowledgeChunk] = []
        self.doc_lengths: List[int] = []
        self.avg_doc_len: float = 0.0
        self.doc_freqs: Dict[str, int] = Counter()
        self.idf: Dict[str, float] = {}
        self.tokenized_docs: List[List[str]] = []
        self._is_indexed: bool = False
        self.initialize_index()

    def tokenize(self, text: str) -> List[str]:
        """Telecom-adapted tokenization preserving error codes, hyphenated models, and plan codes."""
        # Preserves tokens like ERR_NW_ATTACH_TIMEOUT_0x4F, GW-AX6000, 0x4F, fast.telco.net
        tokens = re.findall(r"[A-Za-z0-9_\-\.]+", text.lower())
        return tokens

    def initialize_index(self) -> None:
        """Build BM25 term frequency, inverse document frequency, and document length matrices."""
        self.chunks = ingestion_pipeline.load_and_chunk_all()
        if not self.chunks:
            return

        self.tokenized_docs = [
            self.tokenize(f"{c.title} {c.category} {c.content} {' '.join(c.keywords)}")
            for c in self.chunks
        ]
        self.doc_lengths = [len(d) for d in self.tokenized_docs]
        self.avg_doc_len = sum(self.doc_lengths) / max(1, len(self.doc_lengths))

        # Document frequencies
        N = len(self.chunks)
        for doc in self.tokenized_docs:
            unique_terms = set(doc)
            for term in unique_terms:
                self.doc_freqs[term] += 1

        # Inverse Document Frequency (IDF) with Robertson-Spärck Jones smoothing
        for term, freq in self.doc_freqs.items():
            self.idf[term] = math.log(1.0 + (N - freq + 0.5) / (freq + 0.5))

        self._is_indexed = True
        logger.info("BM25 lexical index initialized", total_indexed=N, vocabulary_size=len(self.idf))

    def search(
        self,
        query: str,
        top_k: int = 10,
        metadata_filters: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[KnowledgeChunk, float]]:
        """Compute BM25 scores for query terms across corpus."""
        if not self._is_indexed:
            return []

        query_tokens = self.tokenize(query)
        if not query_tokens:
            return []

        scores: List[float] = [0.0] * len(self.chunks)

        for i, doc in enumerate(self.tokenized_docs):
            doc_len = self.doc_lengths[i]
            term_counts = Counter(doc)

            doc_score = 0.0
            for term in query_tokens:
                if term in term_counts:
                    tf = term_counts[term]
                    idf_val = self.idf.get(term, 0.1)
                    numerator = tf * (self.k1 + 1.0)
                    denominator = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / max(1.0, self.avg_doc_len)))
                    doc_score += idf_val * (numerator / max(0.001, denominator))
            scores[i] = doc_score

        # Apply metadata filters
        candidate_indices = range(len(self.chunks))
        if metadata_filters:
            filtered_indices = []
            for idx in candidate_indices:
                chunk = self.chunks[idx]
                match = True
                for k, v in metadata_filters.items():
                    chunk_val = getattr(chunk, k, chunk.raw_metadata.get(k))
                    if chunk_val != v:
                        match = False
                        break
                if match:
                    filtered_indices.append(idx)
            candidate_indices = filtered_indices

        ranked = sorted(
            [(self.chunks[i], float(scores[i])) for i in candidate_indices],
            key=lambda item: item[1],
            reverse=True,
        )

        return ranked[:top_k]


bm25_engine = BM25Engine()
