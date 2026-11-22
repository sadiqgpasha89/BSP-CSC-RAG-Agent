"""Hierarchical Supervisor Agent (Meta-Planner & Execution Orchestrator)."""

import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
from src.core.config import settings
from src.core.logging import bind_trace_context, clear_trace_context, get_logger
from src.core.hybrid_router import hybrid_router
from src.core.security import security_service
from src.data_engineering.synthetic_data_generator import ACTIVE_OUTAGE_SEVERITIES, data_generator
from src.data_engineering.stream_processor import stream_processor
from src.datascience.pii_scrubber import pii_scrubber
from src.datascience.intent_classifier import intent_classifier
from src.datascience.sentiment_analyzer import sentiment_analyzer
from src.datascience.churn_predictor import churn_predictor
from src.datascience.asr_transcriber import asr_transcriber
from src.rag.hybrid_retriever import hybrid_retriever
from src.agents.state import AgenticSessionState
from src.agents.guardrails import guardrails_service
from src.agents.entities import extract as extract_entities
from src.agents.billing_agent import billing_agent
from src.agents.customer_care_agent import customer_care_agent
from src.agents.knowledge_agent import knowledge_agent
from src.agents.network_agent import network_agent
from src.agents.order_agent import order_agent
from src.agents.retention_agent import retention_agent
from src.agents.sales_agent import sales_agent
from src.agents.sim_device_agent import sim_device_agent
from src.core import i18n
from src.core.proactive import outage_notifier
from src.core.auth import auth_service
from src.core.confirmations import ActionStatus, pending_actions
from src.core.conversation import Conversation, conversation_store
from src.datascience.language import language_detector
from src.hitl.warm_handoff import warm_handoff_manager
from src.tools.gateway import ToolContext, tool_gateway
from src.workflows.control import ControlResult, build_auth, control_flow, start_step_up_message

logger = get_logger(__name__)


class OrchestrationPlanTask(BaseModel):
    task_id: str
    target_agent: str
    instruction: str
    dependencies: List[str] = Field(default_factory=list)
    status: str = "PENDING"  # PENDING, IN_PROGRESS, COMPLETED, FAILED, SKIPPED


class SupervisorExecutionResponse(BaseModel):
    trace_id: str
    session_id: str
    subscriber_id: str
    final_response: str
    intent_detected: str
    is_multi_intent: bool
    sentiment_label: str
    sentiment_score: float = 0.0
    churn_risk: float
    execution_plan: List[Dict[str, Any]]
    steps_executed: int
    retrieved_chunks_count: int
    guardrail_passed: bool
    escalated_to_human: bool
    escalation_reason: Optional[str] = None
    cloud_routing: Dict[str, Any]
    latency_ms: float
    language: str = "en"
    pending_action: Optional[Dict[str, Any]] = None
    requires_step_up: bool = False
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    handover_id: Optional[str] = None
    agents_involved: List[str] = Field(default_factory=list)


class SubscriberNotFoundError(LookupError):
    """Raised when a turn references a subscriber that does not exist in the BSS catalog."""


HUMAN_REQUEST_RE = re.compile(
    r"(\b(talk|speak|chat|connect|transfer)\b.{0,25}\b(human|person|agent|representative|someone|operator|advisor|specialist)\b|\b(real|live) (person|human|agent)\b|"
    r"\bhuman (agent|please)\b|\bhablar con (un|una)? ?(agente|persona|humano)\b|\b(इंसान|एजेंट|इंसान से|किसी व्यक्ति) ?(से)? ?बात)", re.I)
POLICY_QUESTION_RE = re.compile(r"^\W*(how (do|does|can|to|long)|what (happens|do i need|should i)|when (do|does|will)|where can i|is it possible)\b", re.I)
ACCOUNT_MARKER_RE = re.compile(r"(\bINV-|\bTCK-|\bORD-|\bPAY-|\$\s?\d|\bi was\b|\bi want\b|\bplease\b|\bmy (?:line|number|phone|sim)\b.*\b(not|isn't|won't)\b)", re.I)


def is_policy_question(text: str) -> bool:
    """A general 'how does X work' question is answered from the knowledge base, not by acting on the account."""
    return bool(POLICY_QUESTION_RE.search(text)) and not ACCOUNT_MARKER_RE.search(text)


# Execution priority doubles as the topological order of the plan (see _formulate_plan).
AGENT_PRIORITY = {
    "NetworkAgent": 0,
    "SimDeviceAgent": 1,
    "OrderAgent": 2,
    "BillingAgent": 3,
    "CustomerCareAgent": 4,
    "SalesAgent": 5,
    "RetentionAgent": 6,
    "KnowledgeAgent": 7,
}

AGENTS = {
    "NetworkAgent": network_agent, "SimDeviceAgent": sim_device_agent, "OrderAgent": order_agent, "BillingAgent": billing_agent,
    "CustomerCareAgent": customer_care_agent, "SalesAgent": sales_agent, "RetentionAgent": retention_agent, "KnowledgeAgent": knowledge_agent,
}

INTENT_TO_AGENT = {
    "BILLING_DISPUTE": "BillingAgent", "RECHARGE_PAYMENT": "BillingAgent", "NETWORK_OUTAGE_DIAGNOSTICS": "NetworkAgent",
    "PROVISIONING_ESIM": "SimDeviceAgent", "PLAN_SALES": "SalesAgent", "ORDER_STATUS": "OrderAgent", "ACCOUNT_SERVICE": "CustomerCareAgent",
}

HANDOFF_NOTICE = (
    "I've also flagged your case for a Tier-2 specialist, who will receive the full case notes so you won't need to repeat yourself."
)


class SupervisorAgent:
    """Central Meta-Planner orchestrating multi-agent DAG execution, RAG, and guardrails."""

    NAME = "SupervisorAgent"

    def process_turn(
        self,
        user_utterance: str,
        subscriber_id: str,
        session_id: Optional[str] = None,
        historical_sentiments: Optional[List[float]] = None,
        is_audio: bool = False,
        session_token: Optional[str] = None,
        channel: str = "web",
        language_hint: Optional[str] = None,
    ) -> SupervisorExecutionResponse:
        """Execute end-to-end 8-stage cognitive resolution lifecycle."""
        trace_id = f"trc-{uuid.uuid4().hex[:12]}"
        sess_id = session_id or f"sess-{uuid.uuid4().hex[:10]}"
        bind_trace_context(trace_id, sess_id)  # every log line of this turn now carries the correlation ids
        try:
            return self._process_turn(
                user_utterance, subscriber_id, trace_id, sess_id, historical_sentiments, is_audio, session_token, channel, language_hint
            )
        finally:
            clear_trace_context()

    def _process_turn(
        self,
        user_utterance: str,
        subscriber_id: str,
        trace_id: str,
        sess_id: str,
        historical_sentiments: Optional[List[float]],
        is_audio: bool,
        session_token: Optional[str],
        channel: str = "web",
        language_hint: Optional[str] = None,
    ) -> SupervisorExecutionResponse:
        start_time = time.perf_counter()

        # Initialize or hydrate session state. An unknown subscriber is an error, not a license to run the whole
        # pipeline (and issue MFA-level credentials) against a fabricated default profile.
        sub_data = data_generator.get_subscriber(subscriber_id)
        if not sub_data:
            raise SubscriberNotFoundError(f"Subscriber '{subscriber_id}' not found")
        canonical_id = sub_data["subscriber_id"]

        # A session id is not a credential: a session bound to another subscriber raises PermissionError (API -> 403).
        conv = conversation_store.get_or_create(sess_id, canonical_id, channel, language_hint or settings.default_language)
        auth_context = build_auth(conv, canonical_id, sub_data["msisdn"], session_token)

        state = AgenticSessionState(
            session_id=sess_id,
            trace_id=trace_id,
            subscriber_id=canonical_id,
            auth_context=auth_context,
            raw_utterance=user_utterance,
        )

        # Hydrate blackboard
        state.blackboard.update({
            "verified_msisdn": sub_data.get("msisdn"),
            "subscriber_tier": sub_data.get("tier"),
            "subscriber_plan_code": sub_data.get("plan_code"),
            "current_tower_id": sub_data.get("current_tower_id"),
            "assigned_sector": sub_data.get("assigned_sector"),
            "clv_usd": sub_data.get("clv_usd", 1500.0),
            "authenticated_level": auth_context.auth_level,
            "step_up_verified": conv.step_up_active,
            "channel": channel,
            "language": conv.language,
        })
        if historical_sentiments is None and conv.sentiments:
            historical_sentiments = list(conv.sentiments)

        # --- Stage 1: Audio / Ingress & PII Masking ---
        clean_input = user_utterance
        if is_audio:
            asr_res = asr_transcriber.transcribe(user_utterance)
            clean_input = asr_res.normalized_transcript

        pii_res = pii_scrubber.mask(clean_input)
        state.sanitized_utterance = pii_res.masked_text
        state.pii_token_map = pii_res.token_map

        stream_processor.publish(
            topic="ingress_events",
            trace_id=trace_id,
            session_id=sess_id,
            event_type="INGRESS_NORMALIZED",
            payload={"masked_text": pii_res.masked_text, "redacted_count": pii_res.redacted_entities_count},
        )

        # --- Stage 1b: language, human-handover state, and control replies (YES/NO, one-time code, consent) ---
        lang_res = language_detector.detect(clean_input, hint=conv.language)
        if lang_res.confidence > 0 and lang_res.supported:
            conv.language = lang_res.language
            state.blackboard["language"] = conv.language
        elif lang_res.confidence > 0 and not lang_res.supported:
            state.blackboard["unsupported_language"] = lang_res.language

        if conv.handed_over and conv.handover_state in ("PENDING", "ACTIVE"):
            conv.add_turn("user", state.sanitized_utterance, language=conv.language)
            msg = i18n.render("handover_waiting", conv.language, ref=conv.handover_id or "")
            conv.add_turn("assistant", msg, language=conv.language)
            warm_handoff_manager.relay_customer_message(conv.handover_id, state.sanitized_utterance) if conv.handover_id else None
            state.escalate("Conversation is with a human agent")
            return self._finalize_response(state, msg, start_time, conv=conv, already_handed_over=True)

        control = control_flow.try_handle(state, conv, clean_input)
        if control is not None:
            return self._finalize_control(state, conv, control, start_time)

        if HUMAN_REQUEST_RE.search(clean_input):
            state.escalate("Customer asked to speak to a human")
            return self._finalize_response(state, "Of course.", start_time, conv=conv)

        # Slot filling: a short reply that supplies a detail an agent just asked for is merged with the original request.
        slot = conv.notes.pop("awaiting_slot", None)
        if slot and any(k in extract_entities(user_utterance) for k in slot["keys"]):
            merged = f"{slot['original']} {clean_input}"
            state.raw_utterance = f"{slot['original']} {user_utterance}"
            pii_res = pii_scrubber.mask(merged)
            state.sanitized_utterance, state.pii_token_map = pii_res.masked_text, pii_res.token_map
            clean_input = merged

        # A new request supersedes anything the customer left unanswered: stale confirmations are cancelled, not kept armed.
        for stale in pending_actions.open_for_session(conv.session_id, conv.subscriber_id):
            if stale.status == ActionStatus.PENDING_CONFIRMATION:
                tool_gateway.cancel(ToolContext(subscriber_id=state.subscriber_id, session_id=state.session_id, trace_id=state.trace_id, agent=stale.agent,
                                                auth_context=auth_context), stale.action_id)
        conv.notes.pop("resume_request", None)

        # --- Stage 2: Intent, Sentiment & Churn Triage ---
        intent_res = intent_classifier.classify(state.sanitized_utterance)
        state.primary_intent = intent_res.primary_intent
        state.secondary_intents = intent_res.secondary_intents

        sentiment_res = sentiment_analyzer.analyze(state.sanitized_utterance, historical_sentiments)
        state.sentiment_score = sentiment_res.sentiment_score
        state.sentiment_label = sentiment_res.sentiment_label
        state.frustration_velocity = sentiment_res.frustration_velocity

        # Only CRITICAL/MAJOR alarms constitute an outage; a MINOR RF-attenuation warning must not deflect customers.
        outage = network_agent.outage_status(state)
        active_alarms = [{"alarm_id": a["alarm_id"], "sector": outage.get("sector"), "alarm_type": a.get("type"),
                          "estimated_restoration_time": a.get("estimated_restoration_time")} for a in outage.get("alarms", [])]
        is_in_outage = len(active_alarms) > 0

        churn_res = churn_predictor.predict(
            tenure_months=sub_data.get("tenure_months", 12),
            recent_disputes_count=sub_data.get("recent_disputes_count", 0),
            unbilled_usage_usd=sub_data.get("unbilled_usage_usd", 0.0),
            billing_status=sub_data.get("billing_status", "current_paid"),
            current_sentiment=sentiment_res.sentiment_score,
            is_in_outage_sector=is_in_outage,
        )
        state.churn_risk_score = churn_res.churn_probability

        # Update blackboard with entity mentions
        if intent_res.extracted_entities.disputed_amount is not None:
            state.blackboard["disputed_amount"] = intent_res.extracted_entities.disputed_amount

        # --- Edge Case 1: Cascading Network Outage Storm Geofence Deflection (Section 10.1) ---
        # Allow specific diagnostic requests (e.g. user specifically asks to test line or OTDR) to run diagnostics
        if is_in_outage and intent_res.primary_intent in ["NETWORK_OUTAGE_DIAGNOSTICS", "GENERAL_INQUIRY"] and "otdr" not in clean_input.lower():
            alarm = active_alarms[0]
            sub_res = outage_notifier.subscribe(state.subscriber_id, alarm["alarm_id"], alarm.get("sector") or "your area",
                                                alarm.get("estimated_restoration_time"), conv.language, trace_id)
            deflection_msg = (
                f"We are aware of an active infrastructure disruption in your area ({alarm.get('sector')}) "
                f"due to a {str(alarm.get('alarm_type') or 'network event').replace('_', ' ').lower()}. "
                f"Our engineering crews are on-site; estimated full restoration is {alarm.get('estimated_restoration_time')}. "
                + (f"We have registered your line for automated status updates via {str(sub_res['channel']).upper()}."
                   if sub_res["subscribed"] else "I couldn't register you for status updates because service notices are switched off for your contact preferences.")
            )
            state.record_step(
                agent_name=self.NAME,
                action="OUTAGE_STORM_GEOFENCE_DEFLECTION",
                thought="Matched subscriber sector against active critical network alarm. Executing proactive broadcast deflection.",
                tool_output={"success": True, "alarm_id": alarm.get("alarm_id"), "deflection_sent": True},
            )
            return self._finalize_response(state, deflection_msg, start_time, conv=conv)

        # --- Check for Low Intent Confidence Escalation Trigger ---
        if intent_res.requires_hitl_escalation:
            logger.warning("Low intent confidence detected, triggering HITL escalation", conf=intent_res.confidence)
            state.escalate(f"Intent confidence ({intent_res.confidence:.2f}) below threshold ({settings.intent_confidence_threshold})")
            msg = "I am transferring your request directly to a specialized tier-2 human representative to ensure accurate assistance."
            return self._finalize_response(state, msg, start_time, conv=conv)

        # --- Proactive escalation on rapid sentiment collapse (Section 3.3: extreme sentiment degradation) ---
        if sentiment_res.frustration_velocity <= settings.frustration_velocity_threshold:
            logger.warning("Frustration velocity breached threshold, proactive HITL escalation", velocity=sentiment_res.frustration_velocity)
            state.escalate(
                f"Frustration velocity ({sentiment_res.frustration_velocity:.2f}/turn) breached threshold ({settings.frustration_velocity_threshold})"
            )
            msg = (
                "I'm sorry this has been so frustrating. I'm bringing in a senior specialist right now "
                "who will have the full context of your conversation."
            )
            return self._finalize_response(state, msg, start_time, conv=conv)

        # --- Stage 4: Formulate Execution Plan (Plan-and-Solve DAG) ---
        dag_tasks = self._formulate_plan(state, intent_res, churn_res)

        # --- Stage 5 & 6: Execute RAG & Specialized Worker Sub-Agents ---
        # 1. RAG Retrieval
        retrieved = hybrid_retriever.retrieve(
            query=state.sanitized_utterance,
            subscriber_plan_code=sub_data.get("plan_code"),
            top_k=4,
        )
        state.retrieved_chunks = [r.model_dump() for r in retrieved]

        # Check RAG relevance threshold. Zero retrieved chunks is the *worst* relevance, not a pass.
        if intent_res.primary_intent == "GENERAL_INQUIRY":
            if not retrieved:
                state.escalate("No grounded knowledge found for the inquiry")
            elif retrieved[0].rerank_score < settings.rag_relevance_threshold:
                state.escalate(
                    f"RAG relevance score ({retrieved[0].rerank_score:.2f}) below threshold ({settings.rag_relevance_threshold})"
                )

        # 2. Dependency-ordered Worker Agent Execution
        task_status: Dict[str, str] = {}
        for task in dag_tasks:
            # Deterministic loop and step cutoff check (N <= 6)
            if state.step_count >= settings.max_agent_steps:
                logger.warning("Max agent steps reached, forcing plan termination to prevent runaway", steps=state.step_count)
                state.escalate(f"Deterministic execution limit reached ({settings.max_agent_steps} steps). Terminating loop.")
                break

            # Cyclic loop checks: A-B-A-B delegation pattern, or the same agent re-invoked with an identical sub-goal
            if state.detect_cyclic_loop() or state.register_invocation(task.target_agent, task.instruction):
                logger.warning("Cyclic delegation loop detected! Terminating agent loop.", history=state.agent_delegation_history)
                state.escalate("Cyclic delegation loop detected between sub-agents. Immediate human handoff.")
                break

            # Honour DAG edges: never run a task whose prerequisites did not complete (e.g. refund before outage validation)
            unmet = [d for d in task.dependencies if task_status.get(d) != "COMPLETED"]
            if unmet:
                task.status = task_status[task.task_id] = "SKIPPED"
                state.escalate(f"{task.target_agent} skipped: prerequisite task(s) {unmet} did not complete")
                continue

            task.status = "IN_PROGRESS"
            try:
                outcome = self._dispatch_agent_task(state, task)
                task.status = "FAILED" if outcome.get("status") == "failed" else "COMPLETED"
            except Exception as exc:  # last-resort safety net: an agent bug must degrade to a handoff, not a 500
                logger.error("Worker agent raised unexpectedly", agent=task.target_agent, error=str(exc))
                state.record_step(
                    agent_name=task.target_agent,
                    action="UNHANDLED_AGENT_EXCEPTION",
                    tool_name=task.target_agent,
                    tool_output={"success": False, "error": str(exc)},
                    thought="Agent raised an unhandled exception.",
                )
                task.status = "FAILED"
            task_status[task.task_id] = task.status

            # Check consecutive tool failures (HITL trigger: 2 consecutive failures)
            if state.consecutive_tool_failures >= 2:
                logger.warning("Two consecutive tool failures recorded. Triggering HITL handoff.", failures=state.consecutive_tool_failures)
                state.escalate("Two consecutive tool execution failures encountered in backend integrations.")
                break

        # --- Stage 7: Guardrail-Enforced Response Synthesis ---
        factual_draft, notices = self._synthesize_response(state, dag_tasks, retrieved)
        pending_info = self._apply_interaction_requirements(state, conv, notices)
        tool_outputs = [step.tool_output for step in state.execution_stack if step.tool_output]

        if factual_draft:
            guardrail_verdict = guardrails_service.validate(factual_draft, state.retrieved_chunks, tool_outputs)
            state.guardrail_passed = guardrail_verdict.passed
            state.guardrail_violations = guardrail_verdict.violations
            stream_processor.publish(
                topic="guardrail_events",
                trace_id=trace_id,
                session_id=sess_id,
                event_type="GUARDRAIL_VERDICT",
                payload={
                    "passed": guardrail_verdict.passed,
                    "faithfulness_score": guardrail_verdict.faithfulness_score,
                    "violations": guardrail_verdict.violations,
                },
            )
            if guardrail_verdict.passed:
                final_msg = " ".join([guardrail_verdict.sanitized_response] + notices)
            else:
                # The violating draft is withheld entirely; the customer gets the safe fallback and a human takes over.
                state.escalate("Guardrail violation: " + "; ".join(guardrail_verdict.violations))
                final_msg = guardrail_verdict.sanitized_response
        else:
            final_msg = " ".join(notices) if notices else (
                "I've reviewed your request and found nothing that needs action right now. How else may I assist you today?"
            )

        if state.is_escalated_to_human and state.guardrail_passed and HANDOFF_NOTICE not in final_msg:
            final_msg = f"{final_msg}\n\n{HANDOFF_NOTICE}"

        return self._finalize_response(state, final_msg, start_time, dag_tasks, conv=conv, pending=pending_info)

    def _formulate_plan(self, state: AgenticSessionState, intent_res, churn_res) -> List[OrchestrationPlanTask]:
        """Construct a dependency-ordered DAG of worker tasks (one task per agent, topologically sorted).

        Edges encode the spec's intra-task dependencies (Stage 4): *validate the network outage before processing a
        billing request for lack of service*, and *retention is formulated last, after every other task has run*.
        """
        utterance = state.sanitized_utterance
        wanted: Dict[str, str] = {}  # agent -> instruction (dict de-duplicates agents requested by several intents)

        for intent in intent_res.all_intents:
            agent = INTENT_TO_AGENT.get(intent)
            if agent:
                wanted.setdefault(agent, f"{agent} handles {intent}: {utterance}")

        # High churn risk or cancellation intent delegates to the Retention agent (catalogue offers only)
        if churn_res.is_high_churn_risk or "RETENTION_CHURN_RISK" in intent_res.all_intents:
            wanted["RetentionAgent"] = f"Formulate customer retention package for high-risk subscriber ({churn_res.churn_probability:.2f})"

        if is_policy_question(utterance) and "RETENTION_CHURN_RISK" not in intent_res.all_intents:
            wanted = {}
        if not wanted:
            wanted["KnowledgeAgent"] = "Provide grounded answers from verified knowledge base."

        ordered = sorted(wanted, key=lambda agent: AGENT_PRIORITY[agent])
        tasks = [
            OrchestrationPlanTask(task_id=f"TASK-{i}", target_agent=agent, instruction=wanted[agent])
            for i, agent in enumerate(ordered, start=1)
        ]
        by_agent = {t.target_agent: t for t in tasks}

        if "BillingAgent" in by_agent and "NetworkAgent" in by_agent:
            by_agent["BillingAgent"].dependencies.append(by_agent["NetworkAgent"].task_id)
        if "RetentionAgent" in by_agent:
            by_agent["RetentionAgent"].dependencies.extend(t.task_id for t in tasks if t.target_agent != "RetentionAgent")
        return tasks

    def _dispatch_agent_task(self, state: AgenticSessionState, task: OrchestrationPlanTask) -> Dict[str, Any]:
        """Route a task to its worker agent. Agents return an ``AgentOutcome``; the supervisor only reads its status."""
        agent = AGENTS[task.target_agent]
        outcome = agent.run(state, task.instruction)
        return {"status": outcome.status, "agent": task.target_agent}

    def _synthesize_response(
        self,
        state: AgenticSessionState,
        tasks: List[OrchestrationPlanTask],
        retrieved_chunks: List[Any],
    ) -> Tuple[str, List[str]]:
        """Combine agent outcomes into (evidence-derived text, system notices).

        Only the evidence-derived text is run through the factuality guardrail; notices are system-authored templates
        that make no factual claims about the account.
        """
        outcomes = state.blackboard.get("agent_outcomes", {})
        parts: List[str] = []
        notices: List[str] = []
        for task in tasks:
            o = outcomes.get(task.target_agent)
            if not o:
                continue
            parts.extend(m for m in o["messages"] if m)
            notices.extend(n for n in o["notices"] if n)
            if o.get("needs_login"):
                state.blackboard["needs_login"] = True
            if o.get("needs_step_up"):
                state.blackboard["needs_step_up"] = True
            if o.get("clarification") == "consent":
                state.blackboard["awaiting_consent"] = True
        if not parts and not notices and state.primary_intent == "GENERAL_INQUIRY":
            notices.append(i18n.render("not_understood", state.blackboard.get("language", "en")))
        return "\n".join(p for p in parts if p), notices

    def _apply_interaction_requirements(self, state: AgenticSessionState, conv: Conversation, notices: List[str]) -> Optional[Dict[str, Any]]:
        """Turn gateway signals (pending action, step-up, login) into the customer-facing prompt. Returns pending info."""
        lang = conv.language
        pend = state.blackboard.get("pending_actions") or []
        pending_info: Optional[Dict[str, Any]] = None
        if pend:
            keep, extra = pend[0], pend[1:]
            for e in extra:                       # one consequential action at a time: the customer should read exactly one summary
                tool_gateway.cancel(ToolContext(subscriber_id=state.subscriber_id, session_id=state.session_id, trace_id=state.trace_id,
                                                agent=e["agent"], auth_context=state.auth_context), e["action_id"])
                if "I'll take the other request next" not in " ".join(notices):
                    notices.append("I'll take the other request next, once this one is done.")
            act = pending_actions.get(keep["action_id"])
            if keep["status"] == "CONFIRMATION_REQUIRED":
                notices.append(i18n.render("confirm_prompt", lang, summary=keep["summary"], minutes=settings.confirmation_ttl_sec // 60))
            else:
                notices.append(f"This needs a specialist's approval: {keep['summary']}. I've submitted it for review.")
            pending_info = {"action_id": keep["action_id"], "summary": keep["summary"], "status": keep["status"], "expires_at": act.expires_at if act else None}
            conv.pending_action_ids.append(keep["action_id"])
        if state.blackboard.get("needs_step_up"):
            conv.notes["resume_request"] = state.blackboard.get("resume_request")
            notices.append(start_step_up_message(state, conv))
        elif state.blackboard.get("needs_login"):
            notices.append(i18n.render("auth_required", lang))
        if state.blackboard.get("awaiting_slot"):
            conv.notes["awaiting_slot"] = {**state.blackboard["awaiting_slot"], "original": state.sanitized_utterance[:500]}
        if state.blackboard.get("awaiting_consent"):
            conv.notes["awaiting_consent"] = {"purpose": "case_processing"}
            conv.notes["resume_request"] = state.blackboard.get("resume_request") or conv.notes.get("resume_request")
        return pending_info

    def _finalize_response(
        self,
        state: AgenticSessionState,
        final_text: str,
        start_time: float,
        dag_tasks: Optional[List[OrchestrationPlanTask]] = None,
        conv: Optional[Conversation] = None,
        pending: Optional[Dict[str, Any]] = None,
        already_handed_over: bool = False,
    ) -> SupervisorExecutionResponse:
        """Package final execution response, evaluate hybrid cloud routing, and emit analytical telemetry."""
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        # Calculate Token Economics and Hybrid Cloud Routing
        input_tokens = len(state.raw_utterance.split()) * 4 + 200
        output_tokens = len(final_text.split()) * 4
        is_complex = len(state.secondary_intents) > 0 or state.churn_risk_score > 0.75

        routing = hybrid_router.route_inference(
            task_type="supervisor_session",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            is_high_complexity=is_complex,
            contains_pii=bool(state.pii_token_map),  # data residency: PII sessions never leave the on-prem boundary
        )

        state.final_synthesized_response = final_text
        state.is_completed = True

        # Trajectory telemetry (Pipeline 3): one event per reasoning step so the lakehouse trajectory table is populated
        for step in state.execution_stack:
            tool_output = step.tool_output or {}
            failed = tool_output.get("success") is False or "error" in tool_output
            stream_processor.publish(
                topic="agent_trajectory",
                trace_id=state.trace_id,
                session_id=state.session_id,
                event_type="AGENT_REASONING_STEP",
                payload={
                    "agent_name": step.agent_name,
                    "step_index": step.step_index,
                    "action": step.action,
                    "tool_name": step.tool_name,
                    "tool_status": "FAILURE" if failed else ("SUCCESS" if step.tool_name else "NONE"),
                    "latency_ms": step.latency_ms,
                },
            )

        # Emit completion event to lakehouse
        stream_processor.publish(
            topic="session_telemetry",
            trace_id=state.trace_id,
            session_id=state.session_id,
            event_type="SESSION_COMPLETED",
            payload={
                "subscriber_id": state.subscriber_id,
                "duration_sec": duration_ms / 1000.0,
                "total_tokens": input_tokens + output_tokens,
                "cost_usd": routing.estimated_cost_usd,
                "fcr_resolved": not state.is_escalated_to_human,
                "escalated_to_human": state.is_escalated_to_human,
                "customer_sentiment": state.sentiment_score,
                "subscriber_hash": __import__("hashlib").sha256(state.subscriber_id.encode()).hexdigest()[:12],
                "language": (conv.language if conv else "en"),
                "intent": state.primary_intent,
                "escalation_reason": state.escalation_reason,
            },
        )

        handover_id: Optional[str] = None
        if conv is not None:
            conv.add_turn("user", state.sanitized_utterance, intent=state.primary_intent, language=conv.language)
            conv.sentiments.append(state.sentiment_score)
            conv.intents_seen.append(state.primary_intent)
        if state.is_escalated_to_human and not already_handed_over:
            manifest = warm_handoff_manager.create_manifest(state, state.escalation_reason or "Escalated to human tier-2 representative.")
            handover_id = manifest.escalation_id
            if conv is not None:
                conv.handed_over, conv.handover_id, conv.handover_state = True, handover_id, "PENDING"
                final_text = final_text.replace(HANDOFF_NOTICE, i18n.render("handover_notice", conv.language, ref=handover_id))
                if i18n.render("handover_notice", conv.language, ref=handover_id) not in final_text and HANDOFF_NOTICE not in final_text:
                    final_text = f"{final_text}\n\n{i18n.render('handover_notice', conv.language, ref=handover_id)}"
                state.final_synthesized_response = final_text
        elif already_handed_over and conv is not None:
            handover_id = conv.handover_id
        if conv is not None:
            conv.add_turn("assistant", final_text, language=conv.language)

        return SupervisorExecutionResponse(
            trace_id=state.trace_id,
            session_id=state.session_id,
            subscriber_id=state.subscriber_id,
            final_response=final_text,
            intent_detected=state.primary_intent,
            is_multi_intent=len(state.secondary_intents) > 0,
            sentiment_label=state.sentiment_label,
            sentiment_score=round(state.sentiment_score, 3),
            churn_risk=state.churn_risk_score,
            execution_plan=[t.model_dump() for t in (dag_tasks or [])],
            steps_executed=state.step_count,
            retrieved_chunks_count=len(state.retrieved_chunks),
            guardrail_passed=state.guardrail_passed,
            escalated_to_human=state.is_escalated_to_human,
            escalation_reason=state.escalation_reason,
            cloud_routing=routing.model_dump(),
            latency_ms=round(duration_ms, 2),
            language=(conv.language if conv else "en"),
            pending_action=pending,
            requires_step_up=bool(state.blackboard.get("needs_step_up")),
            sources=state.blackboard.get("sources", []),
            handover_id=handover_id,
            agents_involved=sorted({st.agent_name for st in state.execution_stack if st.agent_name != self.NAME}),
        )

    def _finalize_control(self, state: AgenticSessionState, conv: Conversation, control: ControlResult, start_time: float) -> SupervisorExecutionResponse:
        """Finish a turn that answered the system (YES/NO/OTP/consent) rather than asking something new."""
        state.primary_intent = control.intent
        state.sanitized_utterance = state.sanitized_utterance if control.intent not in ("STEP_UP_FAILED", "STEP_UP_LOCKED") else "[verification code]"
        conv.transaction_refs.extend(r for r in control.transaction_refs if r)
        if control.escalate_reason:
            state.escalate(control.escalate_reason)
        if state.blackboard.get("step_up_verified") is None:
            state.blackboard["step_up_verified"] = conv.step_up_active
        state.blackboard["needs_step_up"] = control.requires_step_up
        res = self._finalize_response(state, control.message, start_time, conv=conv, pending=control.pending)
        return res


supervisor_agent = SupervisorAgent()
