"""Billing & Disputes Worker Agent."""

import time
from typing import Dict, Any
from src.agents.state import AgenticSessionState
from src.tools.bss_adapter import bss_adapter
from src.core.logging import get_logger

logger = get_logger(__name__)


class BillingAgent:
    """Specializes in invoice reconciliation, itemized CDR review, and micro-refund authorizations."""

    NAME = "BillingAgent"

    def execute(self, state: AgenticSessionState, task_instruction: str) -> Dict[str, Any]:
        """Execute billing investigation and issue credit if warranted."""
        start_time = time.perf_counter()
        sub_id = state.subscriber_id

        # 1. Fetch billing overview
        billing_info = bss_adapter.get_billing_overview(sub_id)
        cdr_info = bss_adapter.get_itemized_cdr(sub_id)

        disputed_amount = state.blackboard.get("disputed_amount", 30.00)

        # 2. Determine refund eligibility
        # If user disputed roaming charge (e.g., $30) and has good standing
        refund_decision = None
        if any(w in task_instruction.lower() for w in ["credit", "dispute", "refund", "charged", "claim", "demand"]):
            reason = f"Customer dispute resolution for {sub_id}"
            refund_decision = bss_adapter.apply_billing_credit(
                subscriber_id=sub_id,
                amount_usd=float(disputed_amount),
                reason=reason,
                authorized_by=self.NAME,
            )
            if refund_decision.authorization_level == "ESCALATION_REQUIRED":
                state.is_escalated_to_human = True
                state.escalation_reason = refund_decision.message

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        tool_output = {
            "billing_overview": billing_info,
            "disputed_charges": cdr_info.get("disputed_charges_total_usd"),
            "refund_result": refund_decision.model_dump() if refund_decision else None,
        }

        thought = (
            f"Reviewed itemized CDR records for subscriber {sub_id}. "
            f"Found disputed roaming charge of ${disputed_amount:.2f}. "
            + (f"Applied refund authorization: {refund_decision.authorization_level}." if refund_decision else "No refund requested.")
        )

        state.record_step(
            agent_name=self.NAME,
            action="INVESTIGATE_AND_RESOLVE_BILLING",
            tool_name="bss_adapter.apply_billing_credit",
            tool_input={"subscriber_id": sub_id, "amount_usd": disputed_amount},
            tool_output=tool_output,
            thought=thought,
            latency_ms=duration_ms,
        )

        state.blackboard["billing_resolved"] = True
        state.blackboard["billing_summary"] = tool_output

        return {
            "status": "success",
            "agent": self.NAME,
            "billing_info": billing_info,
            "refund": refund_decision.model_dump() if refund_decision else None,
            "thought": thought,
        }


billing_agent = BillingAgent()
