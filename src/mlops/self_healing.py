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

    BASELINE_WEIGHTS = (1.0, 1.0, 1.0)
    REBALANCED_WEIGHTS = (0.8, 1.4, 1.2)

    def __init__(self):
        self.applied_actions: List[SelfHealingAction] = []
        self.throttled_tools: Dict[str, str] = {}

    def evaluate_and_heal(self, drift_report: DriftReport) -> List[SelfHealingAction]:
        """Apply deterministic mitigation policies based on detected drift signals."""
        new_actions: List[SelfHealingAction] = []

        # 1. RAG Semantic Drift Mitigation: Dynamic Weight Rebalancing (idempotent: re-running while the same drift
        # persists does not re-apply, or re-log, an identical change)
        current = (hybrid_retriever.dense_weight, hybrid_retriever.bm25_weight, hybrid_retriever.graph_weight)
        if drift_report.rag_drift_detected or drift_report.concept_drift_detected:
            if current != self.REBALANCED_WEIGHTS:
                # Shift weight to favor sparse lexical retrieval for out-of-distribution jargon
                hybrid_retriever.set_dynamic_weights(*self.REBALANCED_WEIGHTS)
                act = SelfHealingAction(
                    action_type="DYNAMIC_RAG_REBALANCE",
                    target_component="hybrid_retriever",
                    parameter_adjusted="retrieval_weights",
                    old_value=dict(zip(("dense", "bm25", "graph"), current)),
                    new_value=dict(zip(("dense", "bm25", "graph"), self.REBALANCED_WEIGHTS)),
                    justification="Vocabulary shift detected: boosted sparse lexical BM25 matching for novel terms.",
                )
                new_actions.append(act)
                self.applied_actions.append(act)
        elif current == self.REBALANCED_WEIGHTS:
            # Drift cleared: self-healing must be reversible, otherwise a transient anomaly degrades retrieval forever.
            hybrid_retriever.set_dynamic_weights(*self.BASELINE_WEIGHTS)
            act = SelfHealingAction(
                action_type="RAG_WEIGHT_RESTORE",
                target_component="hybrid_retriever",
                parameter_adjusted="retrieval_weights",
                old_value=dict(zip(("dense", "bm25", "graph"), current)),
                new_value=dict(zip(("dense", "bm25", "graph"), self.BASELINE_WEIGHTS)),
                justification="Drift signals cleared: restored baseline retrieval weights.",
            )
            new_actions.append(act)
            self.applied_actions.append(act)

        # 2. Tool Surge Mitigation: Flag tool anomaly
        if drift_report.tool_drift_detected:
            for tool_name, ratio in drift_report.tool_frequency_anomaly.items():
                if ratio >= 2.5 and tool_name not in self.throttled_tools:
                    self.throttled_tools[tool_name] = "throttle_50_req_per_sec"
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
