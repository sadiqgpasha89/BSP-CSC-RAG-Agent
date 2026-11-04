"""Billing & Disputes Worker Agent."""

import re
import time
from typing import Any, Dict, Optional

from src.agents.state import AgenticSessionState
from src.core.config import settings
from src.core.logging import get_logger
from src.core.security import CPNIScope, security_service
from src.tools.bss_adapter import BillingCreditResult, bss_adapter

logger = get_logger(__name__)

# Phrases that mean the *customer is asking for money back*. The previous agent keyed off words in its own
# instruction string ("...authorize credit for..."), which always matched, so every billing question triggered a credit.
REFUND_REQUEST_PATTERN = re.compile(
    r"\b(?:refund|credit|reimburse|reverse|dispute|disputed|charged twice|double[- ]charged|overcharged|"
    r"wrongly charged|unauthori[sz]ed charge|unexpected charge|demand)\b",
    re.IGNORECASE,
)


class BillingAgent:
    """Specializes in invoice reconciliation, itemized CDR review, and micro-refund authorizations."""

    NAME = "BillingAgent"

    def execute(self, state: AgenticSessionState, task_instruction: str) -> Dict[str, Any]:
        """Execute billing investigation and issue a policy-bounded credit only when warranted and verified."""
        start_time = time.perf_counter()
        sub_id = state.subscriber_id

        # 0. CPNI gate: reading billing data requires the billing-view scope
        can_view, deny_reason = security_service.validate_cpni_access(state.auth_context, CPNIScope.VIEW_BILLING)
        if not can_view:
            return self._record(
                state, start_time,
                tool_output={"success": True, "access_denied": deny_reason, "refund_result": None},
                thought=f"Billing access denied by CPNI policy: {deny_reason}",
                summary_note="I need to verify your identity before I can discuss billing details.",
            )

        # 1. Fetch billing overview + itemized CDRs (backend calls may fail or be quarantined by the circuit breaker)
        try:
            billing_info = bss_adapter.get_billing_overview(sub_id)
            cdr_info = bss_adapter.get_itemized_cdr(sub_id)
        except Exception as e:  # CircuitOpenError or transport failure -> recorded as a tool failure, never a 500
            logger.error("BSS lookup failed", error=str(e))
            return self._record(
                state, start_time,
                tool_output={"success": False, "error": f"BSS unavailable: {e}"},
                thought=f"BSS lookup failed: {e}",
            )
        if "error" in billing_info or "error" in cdr_info:
            err = billing_info.get("error") or cdr_info.get("error")
            return self._record(state, start_time, tool_output={"success": False, "error": err}, thought=f"BSS error: {err}")

        verified_disputed = float(cdr_info.get("disputed_charges_total_usd") or 0.0)
        claimed = state.blackboard.get("disputed_amount")
        refund_requested = bool(REFUND_REQUEST_PATTERN.search(state.sanitized_utterance or ""))

        refund_decision: Optional[BillingCreditResult] = None
        if refund_requested:
            refund_decision = self._decide_refund(state, sub_id, claimed, verified_disputed)

        tool_output = {
            "success": True,
            "billing_overview": billing_info,
            "disputed_charges": verified_disputed,
            "refund_requested": refund_requested,
            "refund_result": refund_decision.model_dump() if refund_decision else None,
        }

        if refund_decision is not None:
            thought = (
                f"Reviewed itemized CDR records for {sub_id}; CDR-verified disputed total ${verified_disputed:.2f}, "
                f"customer claim {'$%.2f' % claimed if claimed is not None else 'unspecified'}. "
                f"Refund outcome: {refund_decision.authorization_level}."
            )
        else:
            thought = f"Reviewed billing for {sub_id}; no refund requested (CDR-verified disputed total ${verified_disputed:.2f})."

        return self._record(state, start_time, tool_output=tool_output, thought=thought)

    # ---------------------------------------------------------------- policy
    def _decide_refund(
        self,
        state: AgenticSessionState,
        sub_id: str,
        claimed: Optional[float],
        verified_disputed: float,
    ) -> BillingCreditResult:
        """Apply deterministic fiscal policy. The agent can only auto-credit what the CDRs *prove* is disputed."""
        # A refund must be evidence-based: default the claim to the verified disputed total, never to a magic $30.
        amount = float(claimed) if claimed is not None else verified_disputed

        if amount <= 0:
            result = BillingCreditResult(
                success=False, credited_amount_usd=0.0, new_balance_usd=0.0, authorization_level="REJECTED",
                message="I couldn't find a disputed charge on your latest bill to credit. A billing specialist can review your account further.",
            )
            state.escalate("Refund requested but no CDR-verified disputed charge found")
            return result

        # Above the hard ceiling: the adapter owns the message/level, the agent only routes to a human.
        if amount > settings.max_refund_supervisor_required:
            result = bss_adapter.apply_billing_credit(
                subscriber_id=sub_id, amount_usd=amount, reason=f"Customer dispute resolution for {sub_id}",
                authorized_by=self.NAME, auth_context=state.auth_context,
            )
            state.escalate(result.message)
            return result

        # Needs a human when it exceeds the autonomous limit (supervisor approval cannot be self-granted by the agent)
        # or is not backed by CDR-verified disputed charges (goodwill concession, not a billing error).
        if amount > settings.max_refund_autonomous or amount > verified_disputed + 0.005:
            reason = (
                f"Credit of ${amount:.2f} requires supervisor approval "
                f"(autonomous limit ${settings.max_refund_autonomous:.2f}; CDR-verified disputed charges ${verified_disputed:.2f})."
            )
            state.escalate(reason)
            return BillingCreditResult(
                success=False, credited_amount_usd=0.0, new_balance_usd=0.0,
                authorization_level="ESCALATION_REQUIRED",
                message=f"{reason} I've sent your request to a supervisor for review.",
            )

        result = bss_adapter.apply_billing_credit(
            subscriber_id=sub_id,
            amount_usd=amount,
            reason=f"Customer dispute resolution for {sub_id}",
            authorized_by=self.NAME,
            auth_context=state.auth_context,
            idempotency_key=f"{state.session_id}:{sub_id}:{amount:.2f}",
        )
        if result.authorization_level in ("ESCALATION_REQUIRED", "REJECTED_UNAUTHORIZED"):
            state.escalate(result.message)
        return result

    # --------------------------------------------------------------- plumbing
    def _record(
        self,
        state: AgenticSessionState,
        start_time: float,
        tool_output: Dict[str, Any],
        thought: str,
        summary_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        if summary_note:
            tool_output["customer_note"] = summary_note

        state.record_step(
            agent_name=self.NAME,
            action="INVESTIGATE_AND_RESOLVE_BILLING",
            tool_name="bss_adapter",
            tool_input={"subscriber_id": state.subscriber_id, "claimed_amount_usd": state.blackboard.get("disputed_amount")},
            tool_output=tool_output,
            thought=thought,
            latency_ms=duration_ms,
        )
        ok = tool_output.get("success") is not False
        state.blackboard["billing_resolved"] = ok
        state.blackboard["billing_summary"] = tool_output
        return {
            "status": "success" if ok else "failed",
            "agent": self.NAME,
            "billing_info": tool_output.get("billing_overview"),
            "refund": tool_output.get("refund_result"),
            "thought": thought,
        }


billing_agent = BillingAgent()
