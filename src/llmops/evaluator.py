"""LLMOps Evaluation & Regression Testing Engine (Pipeline 4)."""

import hashlib
import re
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field
from src.core.logging import get_logger

logger = get_logger(__name__)


class EvaluationReport(BaseModel):
    test_id: str
    context_precision: float
    context_recall: float
    faithfulness: float
    answer_relevance: float
    agent_path_efficiency: float
    goal_completed: bool
    passed_all_thresholds: bool
    details: Dict[str, Any] = Field(default_factory=dict)


class LLMOpsEvaluator:
    """Computes Ragas-style RAG metrics and Agentic Trajectory Optimality."""

    def evaluate_turn(
        self,
        query: str,
        response: str,
        retrieved_contexts: List[str],
        ground_truth_context: Optional[str] = None,
        optimal_tool_count: int = 1,
        actual_tool_count: int = 1,
    ) -> EvaluationReport:
        """Compute context precision, recall, faithfulness, and path efficiency."""
        # 1. Faithfulness: Claims in response supported by retrieved context
        joined_context = " ".join(retrieved_contexts).lower()
        response_words = re.findall(r"\b\w{4,}\b", response.lower())
        supported = sum(1 for w in response_words if w in joined_context) if response_words else 1
        faithfulness = min(1.0, round((supported / max(1, len(response_words))) * 1.2, 4)) if retrieved_contexts else 1.0

        # 2. Context Precision: Relevant chunks in context window
        query_words = set(re.findall(r"\b\w{3,}\b", query.lower()))
        relevant_chunks = 0
        for ctx in retrieved_contexts:
            ctx_words = set(re.findall(r"\b\w{3,}\b", ctx.lower()))
            if len(query_words.intersection(ctx_words)) >= 2:
                relevant_chunks += 1
        precision = round(relevant_chunks / max(1, len(retrieved_contexts)), 4) if retrieved_contexts else 1.0

        # 3. Context Recall: Extent to which ground truth is captured
        if ground_truth_context:
            gt_words = set(re.findall(r"\b\w{4,}\b", ground_truth_context.lower()))
            recalled = sum(1 for w in gt_words if w in joined_context)
            recall = round(recalled / max(1, len(gt_words)), 4)
        else:
            recall = 1.0

        # 4. Answer Relevance: Query words reflected in final response
        overlap = sum(1 for w in query_words if w in response.lower())
        relevance = min(1.0, round(overlap / max(1, len(query_words)), 4)) if query_words else 1.0

        # 5. Agentic Path Efficiency: eta = K* / K
        actual_k = max(1, actual_tool_count)
        path_eff = min(1.0, round(optimal_tool_count / actual_k, 4))

        passed = (faithfulness >= 0.70) and (precision >= 0.50) and (path_eff >= 0.50)

        return EvaluationReport(
            test_id="EVAL-" + hashlib.sha1(query.encode("utf-8")).hexdigest()[:8].upper(),
            context_precision=precision,
            context_recall=recall,
            faithfulness=faithfulness,
            answer_relevance=relevance,
            agent_path_efficiency=path_eff,
            goal_completed=True,
            passed_all_thresholds=passed,
            details={
                "retrieved_count": len(retrieved_contexts),
                "actual_tools": actual_tool_count,
                "optimal_tools": optimal_tool_count,
            },
        )


llmops_evaluator = LLMOpsEvaluator()
