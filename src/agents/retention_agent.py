"""Retention & Churn Mitigation Worker Agent."""

import time
from typing import Dict, Any
from src.agents.state import AgenticSessionState
from src.core.logging import get_logger

logger = get_logger(__name__)


class RetentionAgent:
    """Specializes in subscriber retention, CLV preservation, and policy-bounded offers."""

    NAME = "RetentionWorkerAgent"

    def execute(self, state: AgenticSessionState, task_instruction: str) -> Dict[str, Any]:
        """Formulate bounded retention concession to avert subscriber cancellation."""
        start_time = time.perf_counter()
        sub_id = state.subscriber_id
        clv_usd = state.blackboard.get("clv_usd", 2500.00)
        churn_risk = state.churn_risk_score

        # Bound retention concession based on CLV and churn probability
        if clv_usd >= 2000.0:
            offer_title = "VIP Loyalty Continuity Credit"
            concession_desc = "$20/month bill credit for the next 6 months plus complimentary 50GB high-speed hotspot boost."
            financial_value_usd = 120.00
            retained_prob_lift = 0.65
        else:
            offer_title = "Preferred Customer Appreciation Perk"
            concession_desc = "$10/month bill credit for 3 months plus free international roaming passport."
            financial_value_usd = 30.00
            retained_prob_lift = 0.45

        expected_clv_saved = round(clv_usd * retained_prob_lift, 2)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        tool_output = {
            "subscriber_id": sub_id,
            "churn_risk_observed": churn_risk,
            "clv_usd": clv_usd,
            "offer_title": offer_title,
            "concession_details": concession_desc,
            "financial_commitment_usd": financial_value_usd,
            "expected_clv_preserved_usd": expected_clv_saved,
        }

        thought = (
            f"Subscriber {sub_id} flagged with elevated churn risk ({churn_risk:.2f}). "
            f"Account CLV is ${clv_usd:.2f}. "
            f"Prepared bounded retention package '{offer_title}' to protect account equity."
        )

        state.record_step(
            agent_name=self.NAME,
            action="FORMULATE_RETENTION_CONCESSION",
            tool_name="retention_policy_engine",
            tool_input={"subscriber_id": sub_id, "clv_usd": clv_usd, "churn_risk": churn_risk},
            tool_output=tool_output,
            thought=thought,
            latency_ms=duration_ms,
        )

        state.blackboard["retention_package"] = tool_output
        return {
            "status": "success",
            "agent": self.NAME,
            "retention_package": tool_output,
            "thought": thought,
        }


retention_agent = RetentionAgent()
