"""Hierarchical Supervisor Agent (Meta-Planner & Execution Orchestrator)."""

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
from src.agents.billing_agent import billing_agent
from src.agents.network_agent import network_agent
from src.agents.provisioning_agent import provisioning_agent
from src.agents.retention_agent import retention_agent
from src.hitl.warm_handoff import warm_handoff_manager

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


class SubscriberNotFoundError(LookupError):
    """Raised when a turn references a subscriber that does not exist in the BSS catalog."""


# Execution priority doubles as the topological order of the plan (see _formulate_plan).
AGENT_PRIORITY = {
    "NetworkDiagnosticAgent": 0,
    "ProvisioningAgent": 1,
    "BillingAgent": 2,
    "RetentionWorkerAgent": 3,
    "GeneralRAG": 4,
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
    ) -> SupervisorExecutionResponse:
        """Execute end-to-end 8-stage cognitive resolution lifecycle."""
        trace_id = f"trc-{uuid.uuid4().hex[:12]}"
        sess_id = session_id or f"sess-{uuid.uuid4().hex[:10]}"
        bind_trace_context(trace_id, sess_id)  # every log line of this turn now carries the correlation ids
        try:
            return self._process_turn(
                user_utterance, subscriber_id, trace_id, sess_id, historical_sentiments, is_audio, session_token
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
    ) -> SupervisorExecutionResponse:
        start_time = time.perf_counter()

        # Initialize or hydrate session state. An unknown subscriber is an error, not a license to run the whole
        # pipeline (and issue MFA-level credentials) against a fabricated default profile.
        sub_data = data_generator.get_subscriber(subscriber_id)
        if not sub_data:
            raise SubscriberNotFoundError(f"Subscriber '{subscriber_id}' not found")
        canonical_id = sub_data["subscriber_id"]

        auth_context = security_service.resolve_auth_context(
            subscriber_id=canonical_id,
            msisdn=sub_data["msisdn"],
            session_token=session_token,
        )

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
        })

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

        # --- Stage 2: Intent, Sentiment & Churn Triage ---
        intent_res = intent_classifier.classify(state.sanitized_utterance)
        state.primary_intent = intent_res.primary_intent
        state.secondary_intents = intent_res.secondary_intents

        sentiment_res = sentiment_analyzer.analyze(state.sanitized_utterance, historical_sentiments)
        state.sentiment_score = sentiment_res.sentiment_score
        state.sentiment_label = sentiment_res.sentiment_label
        state.frustration_velocity = sentiment_res.frustration_velocity

        # Only CRITICAL/MAJOR alarms constitute an outage; a MINOR RF-attenuation warning must not deflect customers.
        active_alarms = data_generator.get_active_alarms_for_sector(
            sub_data.get("assigned_sector", ""), ACTIVE_OUTAGE_SEVERITIES
        )
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
            deflection_msg = (
                f"We are aware of an active infrastructure disruption in your area ({alarm.get('sector')}) "
                f"due to a {alarm.get('alarm_type', 'network event')}. "
                f"Our engineering crews are on-site; estimated full restoration is {alarm.get('estimated_restoration_time')}. "
                f"We have registered your line for automated status updates via SMS."
            )
            state.record_step(
                agent_name=self.NAME,
                action="OUTAGE_STORM_GEOFENCE_DEFLECTION",
                thought="Matched subscriber sector against active critical network alarm. Executing proactive broadcast deflection.",
                tool_output={"success": True, "alarm_id": alarm.get("alarm_id"), "deflection_sent": True},
            )
            return self._finalize_response(state, deflection_msg, start_time)

        # --- Check for Low Intent Confidence Escalation Trigger ---
        if intent_res.requires_hitl_escalation:
            logger.warning("Low intent confidence detected, triggering HITL escalation", conf=intent_res.confidence)
            state.escalate(f"Intent confidence ({intent_res.confidence:.2f}) below threshold ({settings.intent_confidence_threshold})")
            msg = "I am transferring your request directly to a specialized tier-2 human representative to ensure accurate assistance."
            return self._finalize_response(state, msg, start_time)

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
            return self._finalize_response(state, msg, start_time)

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

        return self._finalize_response(state, final_msg, start_time, dag_tasks)

    def _formulate_plan(self, state: AgenticSessionState, intent_res, churn_res) -> List[OrchestrationPlanTask]:
        """Construct a dependency-ordered DAG of worker tasks (one task per agent, topologically sorted).

        Edges encode the spec's intra-task dependencies (Stage 4): *validate the network outage before processing a
        refund for lack of service*, and *retention is formulated last, after every other task has run*.
        """
        utterance = state.sanitized_utterance
        wanted: Dict[str, str] = {}  # agent -> instruction (dict de-duplicates agents requested by several intents)

        for intent in intent_res.all_intents:
            if intent == "BILLING_DISPUTE":
                wanted.setdefault("BillingAgent", f"Investigate billing charges and reconcile disputed items: {utterance}")
            elif intent == "NETWORK_OUTAGE_DIAGNOSTICS":
                wanted.setdefault("NetworkDiagnosticAgent", f"Diagnose radio and optical line health for {utterance}")
            elif intent == "PROVISIONING_ESIM":
                wanted.setdefault("ProvisioningAgent", f"Process provisioning or SIM verification for {utterance}")

        # High Churn Risk or Cancellation intent delegates to Retention Agent
        if churn_res.is_high_churn_risk or "RETENTION_CHURN_RISK" in intent_res.all_intents:
            wanted["RetentionWorkerAgent"] = f"Formulate customer retention package for high-risk subscriber ({churn_res.churn_probability:.2f})"

        if not wanted:
            wanted["GeneralRAG"] = "Provide grounded answers from verified knowledge base."

        ordered = sorted(wanted, key=lambda agent: AGENT_PRIORITY[agent])
        tasks = [
            OrchestrationPlanTask(task_id=f"TASK-{i}", target_agent=agent, instruction=wanted[agent])
            for i, agent in enumerate(ordered, start=1)
        ]
        by_agent = {t.target_agent: t for t in tasks}

        if "BillingAgent" in by_agent and "NetworkDiagnosticAgent" in by_agent:
            by_agent["BillingAgent"].dependencies.append(by_agent["NetworkDiagnosticAgent"].task_id)
        if "RetentionWorkerAgent" in by_agent:
            by_agent["RetentionWorkerAgent"].dependencies.extend(
                t.task_id for t in tasks if t.target_agent != "RetentionWorkerAgent"
            )
        return tasks

    def _dispatch_agent_task(self, state: AgenticSessionState, task: OrchestrationPlanTask) -> Dict[str, Any]:
        """Route task to corresponding worker agent instance and return its outcome."""
        agent_name = task.target_agent
        if agent_name == "BillingAgent":
            return billing_agent.execute(state, task.instruction)
        if agent_name == "NetworkDiagnosticAgent":
            return network_agent.execute(state, task.instruction)
        if agent_name == "ProvisioningAgent":
            return provisioning_agent.execute(state, task.instruction)
        if agent_name == "RetentionWorkerAgent":
            return retention_agent.execute(state, task.instruction)
        state.record_step(agent_name=self.NAME, action="RAG_KNOWLEDGE_CONSULTATION", thought="Consulted RAG knowledge base for inquiry resolution.")
        return {"status": "success", "agent": self.NAME}

    def _synthesize_response(
        self,
        state: AgenticSessionState,
        tasks: List[OrchestrationPlanTask],
        retrieved_chunks: List[Any],
    ) -> Tuple[str, List[str]]:
        """Synthesize the response, split into (evidence-derived text, system notices).

        Only the evidence-derived text is run through the factuality guardrail; notices (failure explanations,
        identity prompts) are system-authored templates that make no factual claims about the account.
        """
        parts: List[str] = []    # actionable, evidence-derived statements
        info_parts: List[str] = []  # low-value status lines, shown only when nothing better (e.g. a KB answer) exists
        notices: List[str] = []

        # 1. Billing Summary
        if "billing_summary" in state.blackboard:
            bs = state.blackboard["billing_summary"]
            ref = bs.get("refund_result")
            if bs.get("success") is False:
                notices.append("I'm having trouble reaching our billing system right now, so I couldn't verify or change anything on your account.")
            elif bs.get("access_denied"):
                notices.append(bs.get("customer_note", "I need to verify your identity before I can discuss billing details."))
            elif ref and ref.get("success"):
                parts.append(
                    f"I have reviewed your account and verified the disputed charge. "
                    f"A billing credit of ${ref.get('credited_amount_usd'):.2f} has been approved and applied to your account (Adjustment ID: {ref.get('adjustment_id')}). Your updated unbilled balance is ${ref.get('new_balance_usd'):.2f}."
                )
            elif ref and not ref.get("success"):
                notices.append(ref.get("message", "Billing adjustment could not be completed."))
            else:
                overview = bs.get("billing_overview") or {}
                disputed = bs.get("disputed_charges") or 0.0
                sentence = (
                    f"Your last bill was ${overview.get('last_bill_amount_usd', 0.0):.2f} and your current unbilled usage is "
                    f"${overview.get('unbilled_usage_usd', 0.0):.2f}."
                )
                if disputed > 0:
                    sentence += f" I can see a disputed charge of ${disputed:.2f} on your account; tell me if you'd like me to review it for a credit."
                parts.append(sentence)

        # 2. Network Diagnostics Summary
        if "network_summary" in state.blackboard:
            ns = state.blackboard["network_summary"]
            if ns.get("success") is False:
                notices.append("I couldn't reach our network diagnostics systems just now, so I wasn't able to test your line.")
            else:
                otdr = ns.get("otdr_line_test")
                core_reset = ns.get("core_reset")
                tower = ns.get("tower_telemetry") or {}

                if otdr and otdr.get("line_status") == "FIBER_CUT_DETECTED":
                    parts.append(
                        f"Our optical diagnostic test (OTDR) detected an external optical fiber break at {otdr.get('fault_distance_meters')}m from the distribution hub. "
                        f"A priority field technician dispatch has been created to splice the damaged line."
                    )
                elif otdr:
                    parts.append(f"Our optical diagnostic test (OTDR) found your fiber line healthy: {otdr.get('diagnostic_summary')}")
                if core_reset:
                    parts.append(
                        f"I executed a network signaling refresh on the cellular core for your number. "
                        f"{core_reset.get('instruction_for_customer')}"
                    )
                if tower.get("status") in ("CRITICAL_ALARM", "DEGRADED_WARNING") and not otdr:
                    parts.append(
                        f"Our monitoring shows {tower.get('name')} is reporting {str(tower.get('status')).replace('_', ' ').lower()}"
                        + (f": {tower.get('root_cause')}" if tower.get("root_cause") else ".")
                    )
                elif not otdr and not core_reset and tower.get("status") == "OPERATIONAL":
                    info_parts.append(f"Our monitoring shows {tower.get('name')} operating normally with no active alarms.")
                info_parts.extend(ns.get("info") or [])
                notices.extend(ns.get("notes") or [])

        # 3. Provisioning Summary
        if "provisioning_result" in state.blackboard:
            pr = state.blackboard["provisioning_result"]
            if pr.get("success") is False and pr.get("error"):
                notices.append("Our provisioning system is unavailable right now, so I couldn't process that request.")
            elif pr.get("message"):
                parts.append(pr["message"])

        # 4. Retention Perk Summary
        if "retention_package" in state.blackboard:
            rp = state.blackboard["retention_package"]
            if rp.get("requires_supervisor_approval"):
                parts.append(
                    f"Because you are a valued customer, I've asked a supervisor to approve our {rp.get('offer_title')}: {rp.get('concession_details')}"
                )
            else:
                parts.append(
                    f"Because you are a valued customer with us, I am pleased to offer you our {rp.get('offer_title')}: {rp.get('concession_details')}"
                )

        # 5. RAG Grounded Advice. Knowledge is the answer when the plan is a pure inquiry, or when the agents produced no
        # actionable result and raised no notice. It must NOT paper over a refused action (e.g. a refund routed to a
        # supervisor) with an unrelated document.
        is_knowledge_plan = bool(tasks) and tasks[0].target_agent == "GeneralRAG"
        if retrieved_chunks and not parts and (is_knowledge_plan or not notices):
            top_chunk = retrieved_chunks[0]
            parts.append(
                f"Based on our official documentation regarding '{top_chunk.title}':\n"
                f"{top_chunk.content}"
            )
        elif not parts and info_parts:
            parts.extend(info_parts)

        if not parts and not notices and state.primary_intent == "GENERAL_INQUIRY":
            notices.append("I couldn't find verified information to answer that, so I won't guess.")

        return "\n\n".join(p for p in parts if p), notices

    def _finalize_response(
        self,
        state: AgenticSessionState,
        final_text: str,
        start_time: float,
        dag_tasks: Optional[List[OrchestrationPlanTask]] = None,
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
            },
        )

        if state.is_escalated_to_human:
            warm_handoff_manager.create_manifest(state, state.escalation_reason or "Escalated to human tier-2 representative.")

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
        )


supervisor_agent = SupervisorAgent()
