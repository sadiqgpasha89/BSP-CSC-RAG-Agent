"""Human-In-The-Loop (HITL) Tiered Escalation Criteria Evaluator."""

from typing import Tuple, Optional
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


class EscalationEvaluator:
    """Evaluates deterministic conditions that trigger immediate transfer to Tier-2/Tier-3 human representatives."""

    @staticmethod
    def should_escalate(
        intent_confidence: float,
        rag_confidence: float,
        consecutive_tool_failures: int,
        requested_concession_usd: float = 0.0,
        policy_violated: bool = False,
        is_cyclic_loop: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Evaluate mathematical and business policy thresholds."""
        # 1. Intent confidence threshold
        if intent_confidence < settings.intent_confidence_threshold:
            return True, f"Intent confidence ({intent_confidence:.2f}) is below operational floor ({settings.intent_confidence_threshold})"

        # 2. RAG relevance threshold
        if rag_confidence > 0 and rag_confidence < settings.rag_relevance_threshold:
            return True, f"RAG retrieval relevance ({rag_confidence:.2f}) is below certainty floor ({settings.rag_relevance_threshold})"

        # 3. Two consecutive failed tool invocations
        if consecutive_tool_failures >= 2:
            return True, f"Two consecutive backend system integrations failed ({consecutive_tool_failures})"

        # 4. Financial concessions exceeding automated ceiling
        if requested_concession_usd > settings.max_refund_supervisor_required:
            return True, f"Requested concession of ${requested_concession_usd:.2f} exceeds automated ceiling (${settings.max_refund_supervisor_required:.2f})"

        # 5. Deterministic guardrail or policy breach
        if policy_violated:
            return True, "Deterministic guardrail or regulatory compliance violation detected"

        # 6. Cyclic loop
        if is_cyclic_loop:
            return True, "Sub-agent cyclic delegation loop detected"

        return False, None


escalation_evaluator = EscalationEvaluator()
