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
    status: str = "PENDING"  # PENDING, ACTIVE (claimed), RESOLVED_BY_HUMAN, DISMISSED
    priority: str = "P3"
    summary: str = ""
    suggested_next_action: str = ""
    language: str = "en"
    claimed_by: Optional[str] = None
    claimed_at: Optional[float] = None
    awaiting_approval: List[Dict[str, Any]] = Field(default_factory=list)
    messages: List[Dict[str, Any]] = Field(default_factory=list)   # human<->customer messages after takeover
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
        summary, suggested, priority, lang, approvals = self._brief(state, reason)

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
            recommended_representative_actions=([suggested] if suggested else []) + recs,
            priority=priority, summary=summary, suggested_next_action=suggested, language=lang, awaiting_approval=approvals,
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
    def _brief(state: AgenticSessionState, reason: str):
        """Case summary, suggested next action and priority for the human agent (agent assist). PII-safe: sanitized text only."""
        from src.core.confirmations import ActionStatus, pending_actions
        from src.core.conversation import conversation_store
        conv = conversation_store.get(state.session_id)
        turns = (conv.turns[-6:] if conv else [])
        said = [f"{t.role}: {t.text[:140]}" for t in turns] + [f"user: {(state.sanitized_utterance or '')[:140]}"]
        tools = [f"{st.agent_name}:{st.tool_name}" for st in state.execution_stack if st.tool_name]
        summary = f"Reason: {reason}. Intent: {state.primary_intent}. Sentiment: {state.sentiment_label}. Tried: {', '.join(tools[-5:]) or 'nothing yet'}. Recent: " + " | ".join(said[-4:])
        low = reason.lower()
        if any(k in low for k in ("bypass", "social engineering", "fraud", "locked")):
            suggested, priority = "Verify the customer's identity through a secure out-of-band channel before any account change.", "P1"
        elif "dispute" in low or "refund" in low or "credit" in low:
            suggested, priority = "Open the registered dispute case, review the invoice evidence, and decide on a credit within your authority.", "P2"
        elif "unknown" in low or "unavailable" in low or "not applied" in low:
            suggested, priority = "Reconcile the payment/transaction with the provider before telling the customer anything; do not repeat the charge.", "P2"
        elif "sim" in low or "esim" in low or "port" in low:
            suggested, priority = "Review the pending SIM/eSIM/port approval and verify the customer before approving.", "P2"
        elif state.sentiment_score <= -0.5 or state.frustration_velocity <= -0.3:
            suggested, priority = "Acknowledge the frustration first, then resolve the open issue; the customer has already repeated themselves.", "P2"
        else:
            suggested, priority = "Read the summary, confirm what the customer needs, and continue from where the assistant stopped.", "P3"
        if state.blackboard.get("clv_usd", 0) >= 2000 and priority == "P3":
            priority = "P2"
        approvals = [{"action_id": a.action_id, "tool": a.tool, "summary": a.summary} for a in pending_actions.open_for_session(state.session_id, state.subscriber_id)
                     if a.status == ActionStatus.AWAITING_APPROVAL]
        return summary, suggested, priority, state.blackboard.get("language", "en"), approvals

    # ---------------------------------------------------------------------------- live takeover
    def get(self, escalation_id: str) -> Optional[WarmHandoffManifest]:
        with self._lock:
            return next((m for m in self.queue if m.escalation_id == escalation_id), None)

    def claim(self, escalation_id: str, operator: str) -> Optional[WarmHandoffManifest]:
        from src.core.conversation import conversation_store
        with self._lock:
            m = next((x for x in self.queue if x.escalation_id == escalation_id), None)
            if m is None:
                return None
            if m.status not in ("PENDING", "ACTIVE") or (m.claimed_by and m.claimed_by != operator):
                raise EscalationAlreadyResolvedError(escalation_id)
            m.status, m.claimed_by, m.claimed_at = "ACTIVE", operator, time.time()
        conv = conversation_store.get(m.session_id)
        if conv is not None:
            conv.handover_state, conv.handover_agent_label = "ACTIVE", "a support specialist"
        return m

    def reply(self, escalation_id: str, operator: str, text: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            m = next((x for x in self.queue if x.escalation_id == escalation_id), None)
            if m is None or m.status != "ACTIVE" or m.claimed_by != operator:
                return None
            msg = {"role": "human", "operator_label": "Support specialist", "text": text[:1500], "ts": time.time()}
            m.messages.append(msg)
            return msg

    def relay_customer_message(self, escalation_id: str, text: str) -> None:
        with self._lock:
            m = next((x for x in self.queue if x.escalation_id == escalation_id), None)
            if m is not None and m.status in ("PENDING", "ACTIVE"):
                m.messages.append({"role": "customer", "text": text[:1000], "ts": time.time()})

    def messages_for_session(self, session_id: str, after: float = 0.0) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(x) for m in self.queue if m.session_id == session_id for x in m.messages if x["role"] == "human" and x["ts"] > after]

    @staticmethod
    def _excerpt(text: str, limit: int = 100) -> str:
        return text if len(text) <= limit else text[:limit] + "..."

    def get_pending_queue(self) -> List[WarmHandoffManifest]:
        """Retrieve all pending escalation manifests."""
        with self._lock:
            return sorted((m for m in self.queue if m.status in ("PENDING", "ACTIVE")), key=lambda m: (m.priority, m.created_at))

    def resolve_escalation(self, escalation_id: str, human_notes: str) -> Optional[WarmHandoffManifest]:
        """Mark an escalation as resolved by human agent, persisting the audit notes.

        Returns None when the id is unknown; raises ``EscalationAlreadyResolvedError`` on a double resolution.
        (The notes were previously accepted by the API and silently discarded.)
        """
        with self._lock:
            for m in self.queue:
                if m.escalation_id == escalation_id:
                    if m.status not in ("PENDING", "ACTIVE"):
                        raise EscalationAlreadyResolvedError(escalation_id)
                    m.status = "RESOLVED_BY_HUMAN"
                    m.resolution_notes = human_notes
                    m.resolved_at = time.time()
                    from src.core.conversation import conversation_store
                    conv = conversation_store.get(m.session_id)
                    if conv is not None:
                        conv.handed_over, conv.handover_state = False, "RESOLVED"
                    logger.info("Escalation resolved by human representative", escalation_id=escalation_id)
                    return m
        return None


warm_handoff_manager = WarmHandoffManager()
