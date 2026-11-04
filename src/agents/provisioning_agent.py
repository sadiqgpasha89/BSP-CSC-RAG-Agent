"""Account & Provisioning Worker Agent."""

import re
import time
from typing import Any, Dict, Optional

from src.agents.state import AgenticSessionState
from src.core.logging import get_logger
from src.tools.provisioning_adapter import ProvisioningResult, provisioning_adapter

logger = get_logger(__name__)

# Deterministic social-engineering cues (Section 10.2). They can only *tighten* policy, never relax it.
URGENCY_CUES = (
    "stranded", "emergency", "lost phone", "lost my phone", "urgent", "immediately", "right now", "asap",
    "without pin", "bypass", "no time", "hurry",
)
ESIM_KEYWORDS = ("esim", "qr code", "download profile")
SWAP_KEYWORDS = ("swap sim", "sim swap", "replace sim", "new sim", "swap my sim")
ROAMING_KEYWORDS = ("roaming pass", "wanderlust", "activate roaming")


class ProvisioningAgent:
    """Specializes in SIM/eSIM provisioning, PAC porting codes, and anti-fraud validation."""

    NAME = "ProvisioningAgent"

    def execute(self, state: AgenticSessionState, task_instruction: str) -> Dict[str, Any]:
        """Execute provisioning request with zero-trust validation."""
        start_time = time.perf_counter()
        sub_id = state.subscriber_id
        text = f"{task_instruction} {state.sanitized_utterance}".lower()

        # Urgency is evaluated ONCE and applied to every credential-affecting path. Previously only the explicit
        # "SIM swap" branch checked it, so "I'm stranded, give me a new eSIM QR code" skipped the anti-fraud gate.
        urgency = any(cue in text for cue in URGENCY_CUES)

        device_eid = state.blackboard.get("device_eid", "EID-8904561122334455")
        res: Optional[ProvisioningResult] = None

        try:
            if any(w in text for w in SWAP_KEYWORDS):
                iccid = self._requested_iccid(state)
                if iccid is None and not urgency:
                    res = ProvisioningResult(
                        success=False, action="SIM_SWAP_VALIDATION", target_subscriber_id=sub_id, security_verified=False,
                        message="To proceed with a SIM replacement, please provide the ICCID printed on the new SIM card.",
                    )
                else:
                    res = provisioning_adapter.validate_sim_swap_request(
                        subscriber_id=sub_id,
                        requested_iccid=iccid or "UNSPECIFIED",
                        auth_context=state.auth_context,
                        emotional_urgency_detected=urgency,
                    )
            elif any(w in text for w in ESIM_KEYWORDS):
                res = provisioning_adapter.generate_esim_profile(
                    subscriber_id=sub_id,
                    device_eid=device_eid,
                    auth_context=state.auth_context,
                    emotional_urgency_detected=urgency,
                )
            elif any(w in text for w in ROAMING_KEYWORDS):
                res = provisioning_adapter.activate_roaming_pass(subscriber_id=sub_id)
        except Exception as e:
            logger.error("Provisioning backend failed", error=str(e))
            return self._record(state, start_time, {"success": False, "error": f"Provisioning unavailable: {e}"},
                                f"Provisioning backend failed: {e}")

        if res is None:
            tool_output = {"success": True, "status": "no_provisioning_action_taken"}
            thought = f"No actionable provisioning request recognised for {sub_id}."
        else:
            tool_output = res.model_dump()
            # A zero-trust *denial* is a correct policy outcome, not a tool failure -> keep the success flag out of the
            # failure counter by reporting it under policy_decision instead.
            tool_output["policy_decision"] = "APPROVED" if res.success else "DENIED"
            tool_output["success"] = True
            tool_output["action_succeeded"] = res.success
            thought = f"Provisioning request processed for {sub_id}. Outcome: {res.message}"

        return self._record(state, start_time, tool_output, thought)

    @staticmethod
    def _requested_iccid(state: AgenticSessionState) -> Optional[str]:
        """Recover the ICCID the customer typed (it was masked to a token before reaching the planner)."""
        for token, value in state.pii_token_map.items():
            if token.startswith("<ICCID_") and re.fullmatch(r"89\d{16,18}", value):
                return value
        return None

    def _record(self, state: AgenticSessionState, start_time: float, tool_output: Dict[str, Any], thought: str) -> Dict[str, Any]:
        state.record_step(
            agent_name=self.NAME,
            action="PROVISION_OR_VERIFY_CREDENTIALS",
            tool_name="provisioning_adapter",
            tool_input={"subscriber_id": state.subscriber_id},  # raw utterance deliberately not echoed into the audit trail
            tool_output=tool_output,
            thought=thought,
            latency_ms=(time.perf_counter() - start_time) * 1000.0,
        )
        state.blackboard["provisioning_result"] = tool_output
        succeeded = tool_output.get("action_succeeded", tool_output.get("success", False))
        return {
            "status": "success" if succeeded else "denied_or_pending",
            "agent": self.NAME,
            "result": tool_output,
            "thought": thought,
        }


provisioning_agent = ProvisioningAgent()
