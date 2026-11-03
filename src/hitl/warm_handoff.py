"""Warm Handoff Manifest Generation & Representative Queue Management."""

import time
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field
from src.agents.state import AgenticSessionState
from src.core.logging import get_logger

logger = get_logger(__name__)


class WarmHandoffManifest(BaseModel):
    escalation_id: str
    trace_id: str
    session_id: str
    subscriber_id: str
    msisdn: str
    tier: str
    clv_usd: float
    escalation_reason: str
    root_cause_summary: str
    verified_identity_state: str
    attempted_actions: List[Dict[str, Any]]
    recommended_representative_actions: List[str]
    status: str = "PENDING"  # PENDING, RESOLVED_BY_HUMAN, DISMISSED
    created_at: float = Field(default_factory=time.time)


class WarmHandoffManager:
    """Creates structured dossiers and maintains human escalation queue."""

    def __init__(self):
        self.queue: List[WarmHandoffManifest] = []

    def create_manifest(self, state: AgenticSessionState, reason: str) -> WarmHandoffManifest:
        """Construct structured operational dossier for human representative transfer."""
        actions = []
        for step in state.execution_stack:
            actions.append({
                "step": step.step_index,
                "agent": step.agent_name,
                "action": step.action,
                "tool": step.tool_name,
                "output_status": "SUCCESS" if step.tool_output and not step.tool_output.get("error") else "FAILURE/NONE",
            })

        recs = [
            "Review customer's unbilled charges and recent CDR dispute records.",
            "Verify physical identity in CRM before executing SIM replacement or credential changes.",
            "If fiber disruption is reported, validate OTDR test results and confirm technician dispatch ETA.",
        ]

        manifest = WarmHandoffManifest(
            escalation_id=f"ESC-{int(time.time()*1000)%1000000}",
            trace_id=state.trace_id,
            session_id=state.session_id,
            subscriber_id=state.subscriber_id,
            msisdn=state.blackboard.get("verified_msisdn", "+1-415-555-0199"),
            tier=state.blackboard.get("subscriber_tier", "standard_consumer"),
            clv_usd=state.blackboard.get("clv_usd", 1500.0),
            escalation_reason=reason,
            root_cause_summary=f"Automated flow terminated due to: {reason}. Customer expressed: '{state.raw_utterance[:100]}...'",
            verified_identity_state=state.auth_context.auth_level if state.auth_context else "unverified",
            attempted_actions=actions,
            recommended_representative_actions=recs,
        )

        self.queue.append(manifest)
        logger.info("Warm handoff dossier generated and queued for human representative", escalation_id=manifest.escalation_id)
        return manifest

    def get_pending_queue(self) -> List[WarmHandoffManifest]:
        """Retrieve all pending escalation manifests."""
        return [m for m in self.queue if m.status == "PENDING"]

    def resolve_escalation(self, escalation_id: str, human_notes: str) -> Optional[WarmHandoffManifest]:
        """Mark an escalation as resolved by human agent with audit notes."""
        for m in self.queue:
            if m.escalation_id == escalation_id:
                m.status = "RESOLVED_BY_HUMAN"
                return m
        return None


warm_handoff_manager = WarmHandoffManager()
