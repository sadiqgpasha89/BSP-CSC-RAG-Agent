"""Account & Provisioning Worker Agent."""

import time
from typing import Dict, Any
from src.agents.state import AgenticSessionState
from src.tools.provisioning_adapter import provisioning_adapter
from src.core.logging import get_logger

logger = get_logger(__name__)


class ProvisioningAgent:
    """Specializes in SIM/eSIM provisioning, PAC porting codes, and anti-fraud validation."""

    NAME = "ProvisioningAgent"

    def execute(self, state: AgenticSessionState, task_instruction: str) -> Dict[str, Any]:
        """Execute provisioning request with zero-trust validation."""
        start_time = time.perf_counter()
        sub_id = state.subscriber_id
        instruction_lower = task_instruction.lower()

        res = None
        device_eid = state.blackboard.get("device_eid", "EID-8904561122334455")
        auth_context = state.auth_context

        # 1. eSIM Profile Request
        if any(w in instruction_lower for w in ["esim", "qr code", "download profile"]):
            res = provisioning_adapter.generate_esim_profile(
                subscriber_id=sub_id,
                device_eid=device_eid,
                auth_context=auth_context,
            )

        # 2. SIM Swap Request
        elif any(w in instruction_lower for w in ["swap sim", "sim swap", "replace sim"]):
            urgency = "stranded" in instruction_lower or "emergency" in instruction_lower or "lost phone" in instruction_lower
            res = provisioning_adapter.validate_sim_swap_request(
                subscriber_id=sub_id,
                requested_iccid="8901410329998881112",
                auth_context=auth_context,
                emotional_urgency_detected=urgency,
            )

        # 3. Roaming Pass Request
        elif any(w in instruction_lower for w in ["roaming pass", "wanderlust", "activate roaming"]):
            res = provisioning_adapter.activate_roaming_pass(subscriber_id=sub_id)

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        tool_output = res.model_dump() if res else {"status": "no_provisioning_action_taken"}

        thought = f"Provisioning request processed for {sub_id}. Outcome: {res.message if res else 'None'}"

        state.record_step(
            agent_name=self.NAME,
            action="PROVISION_OR_VERIFY_CREDENTIALS",
            tool_name="provisioning_adapter",
            tool_input={"subscriber_id": sub_id, "instruction": task_instruction},
            tool_output=tool_output,
            thought=thought,
            latency_ms=duration_ms,
        )

        state.blackboard["provisioning_result"] = tool_output
        return {
            "status": "success" if res and res.success else "denied_or_pending",
            "agent": self.NAME,
            "result": tool_output,
            "thought": thought,
        }


provisioning_agent = ProvisioningAgent()
