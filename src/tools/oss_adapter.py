"""Operations Support System (OSS) Enterprise Tool Adapter."""

import time
from typing import Dict, Any, Optional
from pydantic import BaseModel
from src.core.logging import get_logger
from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.circuit_breaker import CircuitBreaker

logger = get_logger(__name__)


class OTDRTestResult(BaseModel):
    olt_port: str
    subscriber_id: str
    optical_power_dbm: float
    line_status: str  # "NORMAL_SYNC", "HIGH_ATTENUATION", "FIBER_CUT_DETECTED"
    fault_distance_meters: Optional[float] = None
    technician_dispatch_needed: bool
    diagnostic_summary: str


class OSSAdapter:
    """Connects to Operations Support Systems for network telemetry, OTDR line tests, and HSS resets."""

    def __init__(self):
        self.circuit = CircuitBreaker("oss_adapter")

    def get_tower_telemetry(self, tower_id: str) -> Dict[str, Any]:
        """Fetch cell tower radio frequency status, backhaul latency, and active alarms.

        Health is *derived from the live alarm feed*; the previous implementation hard-coded TOWER-104 as
        critical and reported every other tower healthy no matter what the EMS alarms said.
        """
        def _fetch():
            alarms = data_generator.load_network_alarms()
            active_alarms = [a for a in alarms if a.get("target_component") == tower_id and a.get("status") == "ACTIVE"]
            node = next((n for n in data_generator.load_network_topology().get("nodes", []) if n.get("id") == tower_id), {})

            severities = {str(a.get("severity", "")).upper() for a in active_alarms}
            if severities & {"CRITICAL", "MAJOR"}:
                status, rf_state, latency = "CRITICAL_ALARM", "DEGRADED", 125.0
            elif active_alarms:
                status, rf_state, latency = "DEGRADED_WARNING", "ATTENUATED", 12.0
            else:
                status, rf_state, latency = "OPERATIONAL", "OPTIMAL", 1.2

            root_cause = next(
                (a.get("root_cause") for a in active_alarms if str(a.get("severity", "")).upper() in {"CRITICAL", "MAJOR"}),
                active_alarms[0].get("root_cause") if active_alarms else None,
            )
            return {
                "tower_id": tower_id,
                "name": node.get("name", f"Cell Tower {tower_id}"),
                "sector": node.get("sector") or (active_alarms[0].get("sector") if active_alarms else "Unknown"),
                "status": status,
                "rf_power_state": rf_state,
                "backhaul_latency_ms": latency,
                "active_alarms": active_alarms,
                "root_cause": root_cause,
            }
        return self.circuit.call(_fetch)

    def _olt_has_active_fault(self, olt_port: str, subscriber_id: str) -> bool:
        """Decide whether the PON is faulted from topology state and the live alarm feed (no hard-coded subscriber ids)."""
        olt_id = olt_port.split("/", 1)[0]
        node = next((n for n in data_generator.load_network_topology().get("nodes", []) if n.get("id") == olt_id), None)
        if node and "fault" in str(node.get("status", "")).lower():
            return True

        sub = data_generator.get_subscriber(subscriber_id)
        if sub:
            for alarm in data_generator.get_active_alarms_for_sector(sub.get("assigned_sector", ""), ("CRITICAL", "MAJOR")):
                if "FIBER_BROADBAND_PON" in alarm.get("impacted_services", []):
                    return True
        return False

    def run_otdr_fiber_test(self, olt_port: str, subscriber_id: str) -> OTDRTestResult:
        """Simulate Optical Time Domain Reflectometer pulse test to locate fiber breaks or optical degradation."""
        def _execute_otdr():
            if self._olt_has_active_fault(olt_port, subscriber_id):
                return OTDRTestResult(
                    olt_port=olt_port,
                    subscriber_id=subscriber_id,
                    optical_power_dbm=-36.5,
                    line_status="FIBER_CUT_DETECTED",
                    fault_distance_meters=320.5,
                    technician_dispatch_needed=True,
                    diagnostic_summary="Fresnel reflection pulse detected catastrophic optical discontinuity (fiber cut) at 320.5 meters from distribution OLT. External municipal construction zone.",
                )
            return OTDRTestResult(
                olt_port=olt_port,
                subscriber_id=subscriber_id,
                optical_power_dbm=-18.2,
                line_status="NORMAL_SYNC",
                fault_distance_meters=None,
                technician_dispatch_needed=False,
                diagnostic_summary="Optical power optimal (-18.2 dBm). Continuous fiber path verified with zero anomalies.",
            )
        return self.circuit.call(_execute_otdr)

    def execute_core_soft_reset(self, msisdn: str) -> Dict[str, Any]:
        """Reset subscriber's Packet Data Protocol (PDP) context in UDM/HSS to remediate error 0x4F."""
        def _execute_reset():
            return {
                "success": True,
                "msisdn": msisdn,
                "action": "UDM_HSS_CORE_PDP_RESET",
                "status": "RESET_SUCCESSFUL",
                "remediation_code": "REMEDIATE_ERR_NW_ATTACH_TIMEOUT_0x4F",
                "instruction_for_customer": "Stale PDP session cleared on core network. Please toggle Airplane Mode on handset for 15 seconds to complete fresh attachment handshake.",
            }
        return self.circuit.call(_execute_reset)


oss_adapter = OSSAdapter()
