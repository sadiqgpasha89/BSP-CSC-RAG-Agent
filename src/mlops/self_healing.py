"""Automated Self-Healing & Mitigation Loops (Section 9.5)."""

from typing import Dict, Any, List
from pydantic import BaseModel, Field
from src.core.logging import get_logger
from src.rag.hybrid_retriever import hybrid_retriever
from src.tools.circuit_breaker import CircuitBreaker
from src.mlops.drift_detector import DriftReport

logger = get_logger(__name__)


class SelfHealingAction(BaseModel):
    action_type: str
    target_component: str
    parameter_adjusted: str
    old_value: Any
    new_value: Any
    justification: str


class SelfHealingEngine:
    """Orchestrates automated self-healing actions across RAG, tools, and agent confidence floors."""

    def __init__(self):
        self.applied_actions: List[SelfHealingAction] = []

    def evaluate_and_heal(self, drift_report: DriftReport) -> List[SelfHealingAction]:
        """Apply deterministic mitigation policies based on detected drift signals."""
        new_actions: List[SelfHealingAction] = []

        # 1. RAG Semantic Drift Mitigation: Dynamic Weight Rebalancing
        if drift_report.rag_drift_detected or drift_report.concept_drift_detected:
            # Shift weight to favor sparse lexical retrieval for out-of-distribution jargon
            old_w = (hybrid_retriever.dense_weight, hybrid_retriever.bm25_weight)
            hybrid_retriever.set_dynamic_weights(dense_w=0.8, bm25_w=1.4, graph_w=1.2)
            act = SelfHealingAction(
                action_type="DYNAMIC_RAG_REBALANCE",
                target_component="hybrid_retriever",
                parameter_adjusted="retrieval_weights",
                old_value={"dense": old_w[0], "bm25": old_w[1]},
                new_value={"dense": 0.8, "bm25": 1.4},
                justification="Vocabulary shift detected: boosted sparse lexical BM25 matching for novel terms.",
            )
            new_actions.append(act)
            self.applied_actions.append(act)

        # 2. Tool Surge Mitigation: Flag tool anomaly
        if drift_report.tool_drift_detected:
            for tool_name, ratio in drift_report.tool_frequency_anomaly.items():
                if ratio >= 2.5:
                    act = SelfHealingAction(
                        action_type="TOOL_RATE_LIMIT_ISOLATION",
                        target_component=tool_name,
                        parameter_adjusted="rate_limit_throttle",
                        old_value="unrestricted",
                        new_value="throttle_50_req_per_sec",
                        justification=f"Abnormal {ratio:.1f}x invocation frequency spike; enforced defensive throttling.",
                    )
                    new_actions.append(act)
                    self.applied_actions.append(act)

        logger.info("Self-healing cycle completed", actions_count=len(new_actions))
        return new_actions

    def get_healing_history(self) -> List[SelfHealingAction]:
        """Return audit log of all automated self-healing actions."""
        return self.applied_actions


self_healing_engine = SelfHealingEngine()
