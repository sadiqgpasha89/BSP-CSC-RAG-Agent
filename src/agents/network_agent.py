"""Network Diagnostics Worker Agent."""

import time
from typing import Any, Dict, Optional

from src.agents.state import AgenticSessionState
from src.core.logging import get_logger
from src.core.security import CPNIScope, security_service
from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.oss_adapter import oss_adapter

logger = get_logger(__name__)

FIBER_KEYWORDS = ("fiber", "broadband", "otdr", "red light", "flashing red", " pon", "router")
ATTACH_KEYWORDS = ("0x4f", "attachment", "no service", "attach timeout", "hss", "udm")


class NetworkAgent:
    """Specializes in cell-tower telemetry, OTDR fiber diagnostics, and OSS core signaling resets."""

    NAME = "NetworkDiagnosticAgent"

    def execute(self, state: AgenticSessionState, task_instruction: str) -> Dict[str, Any]:
        """Execute network line test or attachment reset based on user symptoms."""
        start_time = time.perf_counter()
        sub_id = state.subscriber_id
        # Symptom matching runs on the customer's (sanitized) words plus the planner instruction.
        text = f" {task_instruction} {state.sanitized_utterance}".lower()

        tower_id = state.blackboard.get("current_tower_id")
        olt_port = self._resolve_olt_port(sub_id)

        otdr_result = None
        hss_reset_result = None
        notes = []
        info = []

        try:
            telemetry = oss_adapter.get_tower_telemetry(tower_id) if tower_id else {"status": "UNKNOWN", "active_alarms": []}

            # 1. Fiber broadband symptoms -> OTDR, but only if the subscriber actually has a PON on file
            if any(w in text for w in FIBER_KEYWORDS):
                if olt_port:
                    otdr_result = oss_adapter.run_otdr_fiber_test(olt_port=olt_port, subscriber_id=sub_id)
                else:
                    info.append("No fiber (PON) service is on file for this account, so no optical line test was run.")

            # 2. Cellular attach timeout / 0x4F / no signal -> HSS soft reset (state-changing: needs MODIFY_SERVICES scope)
            if any(w in text for w in ATTACH_KEYWORDS):
                allowed, deny_reason = security_service.validate_cpni_access(state.auth_context, CPNIScope.MODIFY_SERVICES)
                if allowed:
                    msisdn = state.blackboard.get("verified_msisdn")
                    hss_reset_result = oss_adapter.execute_core_soft_reset(msisdn=msisdn) if msisdn else None
                else:
                    notes.append(f"Core signaling reset not performed: {deny_reason}")
        except Exception as e:  # CircuitOpenError / transport failure: record as failed tool call, never a 500
            logger.error("OSS diagnostics failed", error=str(e))
            return self._record(
                state, start_time, tower_id, olt_port,
                {"success": False, "error": f"OSS unavailable: {e}"},
                f"OSS diagnostics failed for subscriber {sub_id}: {e}",
            )

        tool_output = {
            "success": True,
            "tower_telemetry": telemetry,
            "otdr_line_test": otdr_result.model_dump() if otdr_result else None,
            "core_reset": hss_reset_result,
            "notes": notes,
            "info": info,
        }

        thought = (
            f"Evaluated radio and optical infrastructure for subscriber {sub_id}. "
            f"Tower status: {telemetry.get('status')}. "
            + (f"OTDR Line Test result: {otdr_result.line_status}. " if otdr_result else "")
            + ("Core HSS soft reset executed on MSISDN. " if hss_reset_result else "")
        )
        return self._record(state, start_time, tower_id, olt_port, tool_output, thought)

    @staticmethod
    def _resolve_olt_port(subscriber_id: str) -> Optional[str]:
        """The PON port comes from the subscriber's service record (it used to be one hard-coded port for everyone)."""
        sub = data_generator.get_subscriber(subscriber_id) or {}
        return sub.get("olt_port")

    def _record(
        self,
        state: AgenticSessionState,
        start_time: float,
        tower_id: Optional[str],
        olt_port: Optional[str],
        tool_output: Dict[str, Any],
        thought: str,
    ) -> Dict[str, Any]:
        state.record_step(
            agent_name=self.NAME,
            action="DIAGNOSE_NETWORK_INFRASTRUCTURE",
            tool_name="oss_adapter",
            tool_input={"tower_id": tower_id, "olt_port": olt_port, "subscriber_id": state.subscriber_id},
            tool_output=tool_output,
            thought=thought,
            latency_ms=(time.perf_counter() - start_time) * 1000.0,
        )
        ok = tool_output.get("success") is not False
        state.blackboard["network_diagnosed"] = ok
        state.blackboard["network_summary"] = tool_output
        return {
            "status": "success" if ok else "failed",
            "agent": self.NAME,
            "diagnostics": tool_output,
            "thought": thought,
        }


network_agent = NetworkAgent()
