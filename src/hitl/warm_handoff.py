"""Warm Handoff Manifest Generation & Representative Queue Management."""

import threading
import time
import uuid
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
    resolution_notes: Optional[str] = None
    resolved_at: Optional[float] = None


class EscalationAlreadyResolvedError(Exception):
    """Raised when an operator tries to resolve a dossier that is no longer pending."""


class WarmHandoffManager:
    """Creates structured dossiers and maintains human escalation queue."""

    MAX_QUEUE = 5000

    def __init__(self):
        self.queue: List[WarmHandoffManifest] = []
        self._lock = threading.Lock()

    def create_manifest(self, state: AgenticSessionState, reason: str) -> WarmHandoffManifest:
        """Construct structured operational dossier for human representative transfer."""
        actions = []
        for step in state.execution_stack:
            actions.append({
                "step": step.step_index,
                "agent": step.agent_name,
                "action": step.action,
                "tool": step.tool_name,
                "thought": step.thought,
                "output_status": (
                    "FAILURE"
                    if step.tool_output and (step.tool_output.get("success") is False or "error" in step.tool_output)
                    else "SUCCESS" if step.tool_output else "NONE"
                ),
            })

        recs = [
            "Review customer's unbilled charges and recent CDR dispute records.",
            "Verify physical identity in CRM before executing SIM replacement or credential changes.",
            "If fiber disruption is reported, validate OTDR test results and confirm technician dispatch ETA.",
        ]

        manifest = WarmHandoffManifest(
            escalation_id=f"ESC-{uuid.uuid4().hex[:10].upper()}",
            trace_id=state.trace_id,
            session_id=state.session_id,
            subscriber_id=state.subscriber_id,
            msisdn=state.blackboard.get("verified_msisdn", "+1-415-555-0199"),
            tier=state.blackboard.get("subscriber_tier", "standard_consumer"),
            clv_usd=state.blackboard.get("clv_usd", 1500.0),
            escalation_reason=reason,
            # PII-safe: summarise from the *sanitized* utterance. The raw text (cards, SSNs, PINs) used to be copied
            # into a dossier that is served over the operator API and rendered in the console.
            root_cause_summary=f"Automated flow terminated due to: {reason}. Customer expressed: '{self._excerpt(state.sanitized_utterance or '')}'",
            verified_identity_state=state.auth_context.auth_level if state.auth_context else "unverified",
            attempted_actions=actions,
            recommended_representative_actions=recs,
        )

        with self._lock:
            self.queue.append(manifest)
            if len(self.queue) > self.MAX_QUEUE:  # bounded memory: evict oldest already-resolved dossiers first
                resolved = [m for m in self.queue if m.status != "PENDING"]
                drop = resolved[: len(self.queue) - self.MAX_QUEUE] or self.queue[: len(self.queue) - self.MAX_QUEUE]
                self.queue = [m for m in self.queue if m not in drop]
        logger.info("Warm handoff dossier generated and queued for human representative", escalation_id=manifest.escalation_id)
        return manifest

    @staticmethod
    def _excerpt(text: str, limit: int = 100) -> str:
        return text if len(text) <= limit else text[:limit] + "..."

    def get_pending_queue(self) -> List[WarmHandoffManifest]:
        """Retrieve all pending escalation manifests."""
        with self._lock:
            return [m for m in self.queue if m.status == "PENDING"]

    def resolve_escalation(self, escalation_id: str, human_notes: str) -> Optional[WarmHandoffManifest]:
        """Mark an escalation as resolved by human agent, persisting the audit notes.

        Returns None when the id is unknown; raises ``EscalationAlreadyResolvedError`` on a double resolution.
        (The notes were previously accepted by the API and silently discarded.)
        """
        with self._lock:
            for m in self.queue:
                if m.escalation_id == escalation_id:
                    if m.status != "PENDING":
                        raise EscalationAlreadyResolvedError(escalation_id)
                    m.status = "RESOLVED_BY_HUMAN"
                    m.resolution_notes = human_notes
                    m.resolved_at = time.time()
                    logger.info("Escalation resolved by human representative", escalation_id=escalation_id)
                    return m
        return None


warm_handoff_manager = WarmHandoffManager()
