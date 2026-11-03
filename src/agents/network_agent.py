"""Network Diagnostics Worker Agent."""

import time
from typing import Dict, Any
from src.agents.state import AgenticSessionState
from src.tools.oss_adapter import oss_adapter
from src.core.logging import get_logger

logger = get_logger(__name__)


class NetworkAgent:
    """Specializes in cell-tower telemetry, OTDR fiber diagnostics, and OSS core signaling resets."""

    NAME = "NetworkDiagnosticAgent"

    def execute(self, state: AgenticSessionState, task_instruction: str) -> Dict[str, Any]:
        """Execute network line test or attachment reset based on user symptoms."""
        start_time = time.perf_counter()
        sub_id = state.subscriber_id
        instruction_lower = task_instruction.lower()

        tower_id = state.blackboard.get("current_tower_id", "TOWER-104")
        telemetry = oss_adapter.get_tower_telemetry(tower_id)

        otdr_result = None
        hss_reset_result = None

        # 1. Check for fiber broadband issues / red light / OTDR request
        if any(w in instruction_lower for w in ["fiber", "broadband", "otdr", "red light", "flashing red", "pon", "router"]):
            olt_port = "OLT-OPTICAL-01/PON-04"
            otdr_result = oss_adapter.run_otdr_fiber_test(olt_port=olt_port, subscriber_id=sub_id)

        # 2. Check for cellular attach timeout / 0x4F / no signal
        if any(w in instruction_lower for w in ["0x4f", "attachment", "no service", "attach timeout", "hss", "udm"]):
            msisdn = state.blackboard.get("verified_msisdn", "+1-415-555-0199")
            hss_reset_result = oss_adapter.execute_core_soft_reset(msisdn=msisdn)

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        tool_output = {
            "tower_telemetry": telemetry,
            "otdr_line_test": otdr_result.model_dump() if otdr_result else None,
            "core_reset": hss_reset_result,
        }

        thought = (
            f"Evaluated radio and optical infrastructure for subscriber {sub_id}. "
            f"Tower status: {telemetry.get('status')}. "
            + (f"OTDR Line Test result: {otdr_result.line_status}. " if otdr_result else "")
            + (f"Core HSS soft reset executed on MSISDN. " if hss_reset_result else "")
        )

        state.record_step(
            agent_name=self.NAME,
            action="DIAGNOSE_NETWORK_INFRASTRUCTURE",
            tool_name="oss_adapter.run_otdr_fiber_test",
            tool_input={"tower_id": tower_id, "subscriber_id": sub_id},
            tool_output=tool_output,
            thought=thought,
            latency_ms=duration_ms,
        )

        state.blackboard["network_diagnosed"] = True
        state.blackboard["network_summary"] = tool_output

        return {
            "status": "success",
            "agent": self.NAME,
            "diagnostics": tool_output,
            "thought": thought,
        }


network_agent = NetworkAgent()
