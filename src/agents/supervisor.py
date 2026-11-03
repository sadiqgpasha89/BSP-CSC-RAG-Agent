"""Hierarchical Supervisor Agent (Meta-Planner & Execution Orchestrator)."""

import time
import uuid
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field
from src.core.config import settings
from src.core.logging import get_logger
from src.core.hybrid_router import hybrid_router
from src.core.security import security_service
from src.data_engineering.synthetic_data_generator import data_generator
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
    status: str = "PENDING"  # PENDING, IN_PROGRESS, COMPLETED, FAILED


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
    ) -> SupervisorExecutionResponse:
        """Execute end-to-end 8-stage cognitive resolution lifecycle."""
        start_time = time.perf_counter()
        trace_id = f"trc-{uuid.uuid4().hex[:12]}"
        sess_id = session_id or f"sess-{uuid.uuid4().hex[:10]}"

        # Initialize or hydrate session state
        sub_data = data_generator.get_subscriber(subscriber_id) or {
            "subscriber_id": subscriber_id,
            "msisdn": "+1-415-555-0199",
            "tier": "standard_consumer",
            "plan_code": "5G_ULTRA_MAX_2026",
            "clv_usd": 1500.00,
            "current_tower_id": "TOWER-104",
            "assigned_sector": "Sector-7",
            "tenure_months": 12,
            "recent_disputes_count": 0,
            "unbilled_usage_usd": 0.0,
            "billing_status": "current_paid",
        }

        auth_context = security_service.issue_default_auth_context(
            subscriber_id=subscriber_id,
            msisdn=sub_data.get("msisdn", "+1-415-555-0199"),
        )

        state = AgenticSessionState(
            session_id=sess_id,
            trace_id=trace_id,
            subscriber_id=subscriber_id,
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
        state.frustration_velocity = sentiment_res.frustration_velocity

        # Check for active sector outage to evaluate customer churn risk accurately
        active_alarms = data_generator.get_active_alarms_for_sector(sub_data.get("assigned_sector", "Sector-7"))
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
        if intent_res.extracted_entities.disputed_amount:
            state.blackboard["disputed_amount"] = intent_res.extracted_entities.disputed_amount

        # --- Edge Case 1: Cascading Network Outage Storm Geofence Deflection (Section 10.1) ---
        # Allow specific diagnostic requests (e.g. user specifically asks to test line or OTDR) to run diagnostics
        if is_in_outage and intent_res.primary_intent in ["NETWORK_OUTAGE_DIAGNOSTICS", "GENERAL_INQUIRY"] and "otdr" not in clean_input.lower() and "run an otdr test" not in clean_input.lower():
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
                tool_output={"alarm_id": alarm.get("alarm_id"), "deflection_sent": True},
            )
            return self._finalize_response(state, deflection_msg, start_time)

        # --- Check for Low Intent Confidence Escalation Trigger ---
        if intent_res.requires_hitl_escalation:
            logger.warning("Low intent confidence detected, triggering HITL escalation", conf=intent_res.confidence)
            state.is_escalated_to_human = True
            state.escalation_reason = f"Intent confidence ({intent_res.confidence:.2f}) below threshold ({settings.intent_confidence_threshold})"
            msg = "I am transferring your request directly to a specialized tier-2 human representative to ensure accurate assistance."
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

        # Check RAG relevance threshold
        top_rag_score = retrieved[0].rerank_score if retrieved else 0.0
        if retrieved and top_rag_score < settings.rag_relevance_threshold and intent_res.primary_intent == "GENERAL_INQUIRY":
            state.is_escalated_to_human = True
            state.escalation_reason = f"RAG relevance score ({top_rag_score:.2f}) below threshold ({settings.rag_relevance_threshold})"

        # 2. Sequential / Dependent Worker Agent Execution
        for task in dag_tasks:
            # Deterministic loop and step cutoff check (N <= 6)
            if state.step_count >= settings.max_agent_steps:
                logger.warning("Max agent steps reached, forcing plan termination to prevent runaway", steps=state.step_count)
                state.is_escalated_to_human = True
                state.escalation_reason = f"Deterministic execution limit reached ({settings.max_agent_steps} steps). Terminating loop."
                break

            # Cyclic loop check
            if state.detect_cyclic_loop():
                logger.warning("Cyclic delegation loop detected! Terminating agent loop.", history=state.agent_delegation_history)
                state.is_escalated_to_human = True
                state.escalation_reason = "Cyclic delegation loop detected between sub-agents. Immediate human handoff."
                break

            # Execute Target Agent
            task.status = "IN_PROGRESS"
            self._dispatch_agent_task(state, task)
            task.status = "COMPLETED"

            # Check consecutive tool failures (HITL trigger: 2 consecutive failures)
            if state.consecutive_tool_failures >= 2:
                logger.warning("Two consecutive tool failures recorded. Triggering HITL handoff.", failures=state.consecutive_tool_failures)
                state.is_escalated_to_human = True
                state.escalation_reason = "Two consecutive tool execution failures encountered in backend integrations."
                break

        # --- Stage 7: Guardrail-Enforced Response Synthesis ---
        draft_response = self._synthesize_response(state, dag_tasks, retrieved)
        tool_outputs = [step.tool_output for step in state.execution_stack if step.tool_output]
        guardrail_verdict = guardrails_service.validate(draft_response, state.retrieved_chunks, tool_outputs)
        state.guardrail_passed = guardrail_verdict.passed
        state.guardrail_violations = guardrail_verdict.violations

        stream_processor.publish(
            topic="guardrail_events",
            trace_id=trace_id,
            session_id=sess_id,
            event_type="GUARDRAIL_VERDICT",
            payload={"passed": guardrail_verdict.passed, "violations": guardrail_verdict.violations},
        )

        final_msg = guardrail_verdict.sanitized_response
        return self._finalize_response(state, final_msg, start_time, dag_tasks)

    def _formulate_plan(self, state: AgenticSessionState, intent_res, churn_res) -> List[OrchestrationPlanTask]:
        """Construct structured DAG tasks for specialized worker agents."""
        tasks: List[OrchestrationPlanTask] = []
        counter = 1

        # Multi-intent decomposition
        for intent in intent_res.all_intents:
            if intent == "BILLING_DISPUTE":
                tasks.append(
                    OrchestrationPlanTask(
                        task_id=f"TASK-{counter}",
                        target_agent="BillingAgent",
                        instruction=f"Investigate billing charges and authorize credit for {state.sanitized_utterance}",
                    )
                )
                counter += 1
            elif intent == "NETWORK_OUTAGE_DIAGNOSTICS":
                tasks.append(
                    OrchestrationPlanTask(
                        task_id=f"TASK-{counter}",
                        target_agent="NetworkDiagnosticAgent",
                        instruction=f"Diagnose radio and optical line health for {state.sanitized_utterance}",
                    )
                )
                counter += 1
            elif intent == "PROVISIONING_ESIM":
                tasks.append(
                    OrchestrationPlanTask(
                        task_id=f"TASK-{counter}",
                        target_agent="ProvisioningAgent",
                        instruction=f"Process provisioning or SIM verification for {state.sanitized_utterance}",
                    )
                )
                counter += 1

        # High Churn Risk or Cancellation intent delegates to Retention Agent
        if churn_res.is_high_churn_risk or "RETENTION_CHURN_RISK" in intent_res.all_intents:
            tasks.append(
                OrchestrationPlanTask(
                    task_id=f"TASK-{counter}",
                    target_agent="RetentionWorkerAgent",
                    instruction=f"Formulate customer retention package for high-risk subscriber ({churn_res.churn_probability:.2f})",
                )
            )

        if not tasks:
            tasks.append(
                OrchestrationPlanTask(
                    task_id="TASK-1",
                    target_agent="GeneralRAG",
                    instruction="Provide grounded answers from verified knowledge base.",
                )
            )

        return tasks

    def _dispatch_agent_task(self, state: AgenticSessionState, task: OrchestrationPlanTask) -> None:
        """Route task to corresponding worker agent instance."""
        agent_name = task.target_agent
        if agent_name == "BillingAgent":
            billing_agent.execute(state, task.instruction)
        elif agent_name == "NetworkDiagnosticAgent":
            network_agent.execute(state, task.instruction)
        elif agent_name == "ProvisioningAgent":
            provisioning_agent.execute(state, task.instruction)
        elif agent_name == "RetentionWorkerAgent":
            retention_agent.execute(state, task.instruction)
        else:
            state.record_step(agent_name="SupervisorAgent", action="RAG_KNOWLEDGE_CONSULTATION", thought="Consulted RAG knowledge base for inquiry resolution.")

    def _synthesize_response(
        self,
        state: AgenticSessionState,
        tasks: List[OrchestrationPlanTask],
        retrieved_chunks: List[Any],
    ) -> str:
        """Synthesize natural, empathetic, and factual response incorporating agent outputs."""
        parts: List[str] = []

        # 1. Billing Summary
        if "billing_summary" in state.blackboard:
            bs = state.blackboard["billing_summary"]
            ref = bs.get("refund_result")
            if ref and ref.get("success"):
                parts.append(
                    f"I have reviewed your account and verified the disputed charge. "
                    f"A billing credit of ${ref.get('credited_amount_usd'):.2f} has been approved and applied to your account (Adjustment ID: {ref.get('adjustment_id')}). Your updated unbilled balance is ${ref.get('new_balance_usd'):.2f}."
                )
            elif ref and not ref.get("success"):
                parts.append(ref.get("message", "Billing adjustment could not be completed."))

        # 2. Network Diagnostics Summary
        if "network_summary" in state.blackboard:
            ns = state.blackboard["network_summary"]
            otdr = ns.get("otdr_line_test")
            core_reset = ns.get("core_reset")
            tower = ns.get("tower_telemetry", {})

            if otdr and otdr.get("line_status") == "FIBER_CUT_DETECTED":
                parts.append(
                    f"Our optical diagnostic test (OTDR) detected an external optical fiber break at {otdr.get('fault_distance_meters')}m from the distribution hub. "
                    f"A priority field technician dispatch has been created to splice the damaged line."
                )
            elif core_reset:
                parts.append(
                    f"I executed a network signaling refresh on the cellular core for your number. "
                    f"{core_reset.get('instruction_for_customer')}"
                )

        # 3. Provisioning Summary
        if "provisioning_result" in state.blackboard:
            pr = state.blackboard["provisioning_result"]
            parts.append(pr.get("message", ""))

        # 4. Retention Perk Summary
        if "retention_package" in state.blackboard:
            rp = state.blackboard["retention_package"]
            parts.append(
                f"Because you are a valued customer with us, I am pleased to offer you our {rp.get('offer_title')}: {rp.get('concession_details')}"
            )

        # 5. RAG Grounded Advice (if knowledge consulted)
        if retrieved_chunks and not parts:
            top_chunk = retrieved_chunks[0]
            parts.append(
                f"Based on our official documentation regarding '{top_chunk.title}':\n"
                f"{top_chunk.content}"
            )

        if not parts:
            parts.append("Your account has been reviewed and all services are operating within standard parameters. How else may I assist you today?")

        return "\n\n".join(parts)

    def _finalize_response(
        self,
        state: AgenticSessionState,
        final_text: str,
        start_time: float,
        dag_tasks: List[OrchestrationPlanTask] = None,
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
        )

        state.final_synthesized_response = final_text
        state.is_completed = True

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
            sentiment_label="CRITICAL" if state.sentiment_score <= -0.6 else "NEUTRAL",
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
