"""RAG Retrieval & Generation Metrics (Section 8.3)."""

from typing import List

from pydantic import BaseModel, Field

from src.data_engineering.lakehouse_sink import lakehouse_sink
from src.metrics.system_metrics import MIN_LIVE_SAMPLES


class RAGMetrics(BaseModel):
    context_precision: float
    context_recall: float
    faithfulness_score: float
    answer_relevance: float
    hallucination_frequency_pct: float
    mean_rrf_score: float
    data_source: str = "reference_baseline"
    reference_fields: List[str] = Field(default_factory=list)


class RAGMetricsCalculator:
    """Aggregates multi-tier retrieval quality and factual groundedness scores."""

    REFERENCE = dict(
        context_precision=0.914,   # needs labelled relevance judgements (see LLMOps offline evaluator)
        context_recall=0.886,
        faithfulness_score=0.962,
        answer_relevance=0.938,
        hallucination_frequency_pct=1.4,
        mean_rrf_score=0.0385,
    )

    def compute(self) -> RAGMetrics:
        """Faithfulness and hallucination frequency come from measured guardrail verdicts once enough turns exist."""
        values = dict(self.REFERENCE)
        reference = list(values)
        source = "reference_baseline"

        df = lakehouse_sink.query_guardrails_dataframe()
        if len(df) >= MIN_LIVE_SAMPLES:
            values["faithfulness_score"] = round(float(df["faithfulness_score"].astype(float).mean()), 4)
            hallucinated = df["violations"].apply(lambda v: any("Factuality Hallucination" in x for x in (v or [])))
            values["hallucination_frequency_pct"] = round(float(hallucinated.mean()) * 100.0, 2)
            reference = [f for f in reference if f not in ("faithfulness_score", "hallucination_frequency_pct")]
            source = "live"

        return RAGMetrics(**values, data_source=source, reference_fields=reference)


rag_metrics_calculator = RAGMetricsCalculator()
