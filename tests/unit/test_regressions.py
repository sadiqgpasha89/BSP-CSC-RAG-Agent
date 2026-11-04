"""Regression tests for defects found during the engineering review.

Each test pins a specific, previously-reproducible failure so it cannot silently return.
"""

import time

import pytest

from src.agents.guardrails import SAFE_FALLBACK_RESPONSE, guardrails_service
from src.agents.state import AgenticSessionState
from src.agents.supervisor import SubscriberNotFoundError, supervisor_agent
from src.core.config import Settings, settings
from src.core.hybrid_router import hybrid_router
from src.core.logging import get_logger
from src.core.security import AuthContext, CPNIScope, SecurityService, security_service
from src.data_engineering.stream_processor import StreamProcessor
from src.data_engineering.synthetic_data_generator import SyntheticDataGenerator
from src.datascience.intent_classifier import intent_classifier
from src.datascience.pii_scrubber import pii_scrubber
from src.hitl.warm_handoff import EscalationAlreadyResolvedError, warm_handoff_manager
from src.mlops.drift_detector import DriftReport
from src.mlops.self_healing import SelfHealingEngine
from src.rag.bm25_engine import bm25_engine
from src.rag.graph_store import graph_retriever
from src.rag.hybrid_retriever import hybrid_retriever
from src.rag.temporal_filter import temporal_filter_engine
from src.rag.vector_store import vector_store
from src.tools.bss_adapter import bss_adapter
from src.tools.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitOpenError, CircuitState
from src.tools.oss_adapter import oss_adapter
from src.tools.provisioning_adapter import provisioning_adapter


# --------------------------------------------------------------------------- config / logging
def test_data_paths_do_not_depend_on_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert SyntheticDataGenerator().get_subscriber("SUB-882194") is not None


def test_non_development_environment_requires_real_secrets():
    with pytest.raises(ValueError):
        Settings(environment="production")  # default dev secret + no admin key
    ok = Settings(environment="production", secret_key="x" * 40, admin_api_key="k" * 16)
    assert ok.default_auth_level == "basic_pin"  # fails closed in production


def test_module_loggers_are_lazy_so_configure_logging_applies():
    # An eagerly .bind()-ed logger is a BoundLogger built from structlog defaults at import time.
    assert type(get_logger("x")).__name__ == "BoundLoggerLazyProxy"


# --------------------------------------------------------------------------- NLP triage
def test_intent_confidence_reflects_evidence_instead_of_being_pinned():
    weak = intent_classifier.classify("my bill is high")          # one lexical hit
    strong = intent_classifier.classify("I was charged twice, please refund my bill")
    assert weak.confidence < strong.confidence <= 0.98
    assert intent_classifier.classify("zzz qqq").confidence == intent_classifier.GENERAL_FALLBACK_CONFIDENCE


def test_intent_escalation_is_reachable_when_threshold_is_raised(monkeypatch):
    monkeypatch.setattr(settings, "intent_confidence_threshold", 0.95)
    assert intent_classifier.classify("my bill is high").requires_hitl_escalation is True


def test_pii_masks_parenthesised_and_plus_prefixed_phone_numbers():
    text = "Reach me on (415) 555-0199 or +1-415-555-0123"
    res = pii_scrubber.mask(text)
    assert "555-0199" not in res.masked_text and "555-0123" not in res.masked_text
    assert pii_scrubber.unmask(res.masked_text, res.token_map) == text


def test_pii_distinct_pins_get_distinct_tokens():
    res = pii_scrubber.mask("my pin is 8492 and my password: hunter22")
    assert "8492" not in res.masked_text and "hunter22" not in res.masked_text
    assert sorted(res.token_map.values()) == ["8492", "hunter22"]


# --------------------------------------------------------------------------- security
def test_auth_context_defaults_fail_closed():
    ctx = AuthContext(session_id="s", subscriber_id="SUB-1", msisdn="+1")
    assert ctx.is_mfa_verified is False and ctx.granted_scopes == []
    assert security_service.validate_cpni_access(ctx, CPNIScope.VIEW_BILLING)[0] is False
    assert security_service.validate_cpni_access(None, CPNIScope.VIEW_BILLING)[0] is False


def test_session_token_roundtrip_tamper_and_subscriber_binding():
    svc = SecurityService("k" * 40)
    token = svc.generate_session_token("SUB-882194", "+14155550199", "authenticated_mfa")
    assert svc.verify_session_token(token) == ("SUB-882194", "+14155550199", "authenticated_mfa")
    assert svc.verify_session_token(token.replace("authenticated_mfa", "in_person_verified")) is None

    elevated = svc.resolve_auth_context("SUB-882194", "+14155550199", token)
    assert elevated.is_mfa_verified and CPNIScope.HIGH_RISK_CREDENTIAL in elevated.granted_scopes
    # A token minted for someone else must not elevate this subscriber
    other = svc.resolve_auth_context("SUB-102948", "+14155550288", token)
    assert other.auth_level == "unauthenticated" and other.granted_scopes == []


def test_production_session_without_token_cannot_provision_esim_or_credit(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    res = supervisor_agent.process_turn("I need an eSIM QR code for my new phone.", "SUB-773019")
    assert "SM-DP+" not in res.final_response
    assert "Zero-Trust" in res.final_response

    res = supervisor_agent.process_turn("I was charged twice ($30). Refund this please!", "SUB-882194")
    assert "Adjustment ID" not in res.final_response


# --------------------------------------------------------------------------- guardrails
def test_blocked_draft_is_withheld_not_delivered():
    verdict = guardrails_service.validate(
        "Your card 4532 1234 5678 8910 is on file.", [{"content": "Customer billing details"}]
    )
    assert verdict.passed is False
    assert verdict.sanitized_response == SAFE_FALLBACK_RESPONSE
    assert "4532" not in verdict.sanitized_response


def test_numeric_claims_must_be_grounded_in_evidence():
    ctx = [{"content": "Strict 10GB high-speed mobile hotspot limit per billing cycle."}]
    assert guardrails_service.validate("Your hotspot allowance is 10GB per billing cycle.", ctx).passed
    bad = guardrails_service.validate("Your hotspot allowance is 50GB per billing cycle.", ctx)
    assert bad.passed is False and any("Ungrounded numeric" in v for v in bad.violations)


def test_faithfulness_uses_whole_words_not_substrings():
    # 'net' is a substring of 'network' but not evidence for it
    score = guardrails_service._calculate_faithfulness("fastnet speeds", "network operations manual")
    assert score < 0.65


# --------------------------------------------------------------------------- RAG
def test_legacy_subscriber_can_still_retrieve_plan_agnostic_knowledge():
    filters = temporal_filter_engine.build_filters("FREEDOM_UNLIMITED_2018", "optical fiber diagnostics")
    hits = bm25_engine.search("OTDR fiber cut diagnostics", metadata_filters=filters)
    assert hits, "plan-agnostic SOPs must pass a plan_code filter"
    assert all(c.plan_code in (None, "FREEDOM_UNLIMITED_2018") for c, _ in hits)


def test_unknown_plan_never_sees_grandfathered_terms():
    assert temporal_filter_engine.build_filters(None, "hotspot limit") == {"is_legacy": False}
    assert temporal_filter_engine.build_filters("SOMETHING_NEW", "hotspot limit") == {"is_legacy": False}


def test_graph_tier_respects_temporal_isolation():
    legacy = {"plan_code": "FREEDOM_UNLIMITED_2018"}
    ids = [c.chunk_id for c, _ in graph_retriever.search_relational_context("tell me about 5G_ULTRA_MAX_2026 hotspot", metadata_filters=legacy)]
    assert "GRAPH-PLAN:5G_ULTRA_MAX_2026" not in ids and "GRAPH-FEATURE:50GB_HOTSPOT" not in ids

    result = hybrid_retriever.retrieve("what does 50GB_HOTSPOT give me", subscriber_plan_code="FREEDOM_UNLIMITED_2018")
    assert all("50GB" not in r.content for r in result)


def test_graph_entity_match_requires_whole_token():
    assert not graph_retriever.search_relational_context("status of tower-10 please")
    assert graph_retriever.search_relational_context("status of tower-104 please")


def test_bm25_ignores_sentence_punctuation_and_zero_overlap():
    assert bm25_engine.tokenize("Run the OTDR test.") == ["run", "the", "otdr", "test"]
    assert bm25_engine.search("qqqzzz nonsense") == []
    assert vector_store.search("qqqzzz nonsense") == []


# --------------------------------------------------------------------------- tools
def test_circuit_breaker_open_state_raises_dedicated_error_and_single_probe():
    cb = CircuitBreaker("t", CircuitBreakerConfig(failure_threshold=1, recovery_timeout_sec=0.05))

    def boom():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        cb.call(boom)
    assert cb.state == CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        cb.call(lambda: 1)

    time.sleep(0.06)
    with pytest.raises(ConnectionError):  # the probe fails -> re-open immediately (no threshold re-accumulation)
        cb.call(boom)
    assert cb.state == CircuitState.OPEN and cb.tripped_count == 2  # initial trip + re-trip from the failed probe

    time.sleep(0.06)
    assert cb.call(lambda: "ok") == "ok" and cb.state == CircuitState.CLOSED


def test_bss_rejects_non_positive_amounts_and_unknown_subscribers():
    for bad in (-50.0, 0.0, float("nan"), float("inf")):
        res = bss_adapter.apply_billing_credit("SUB-882194", bad, "x")
        assert res.success is False and res.credited_amount_usd == 0.0
    assert bss_adapter.apply_billing_credit("SUB-DOES-NOT-EXIST", 10.0, "x").success is False


def test_bss_credit_is_idempotent_per_key():
    a = bss_adapter.apply_billing_credit("SUB-882194", 20.0, "dup", idempotency_key="idem-1")
    b = bss_adapter.apply_billing_credit("SUB-882194", 20.0, "dup", idempotency_key="idem-1")
    assert a.success and a.adjustment_id == b.adjustment_id


def test_bss_credit_requires_financial_scope_when_context_supplied():
    ctx = security_service.issue_default_auth_context("SUB-882194", "+1-415-555-0199", "basic_pin")
    res = bss_adapter.apply_billing_credit("SUB-882194", 10.0, "x", auth_context=ctx)
    assert res.success is False and res.authorization_level == "REJECTED_UNAUTHORIZED"


def test_esim_provisioning_fails_closed_without_context_or_with_urgency(sample_auth_context):
    assert provisioning_adapter.generate_esim_profile("SUB-1", "EID-1", None).success is False
    urgent = provisioning_adapter.generate_esim_profile("SUB-1", "EID-1", sample_auth_context, emotional_urgency_detected=True)
    assert urgent.success is False and "Anti-Fraud" in urgent.message
    ok = provisioning_adapter.generate_esim_profile("SUB-1", "EID-1", sample_auth_context)
    ok2 = provisioning_adapter.generate_esim_profile("SUB-1", "EID-1", sample_auth_context)
    assert ok.success and ok.activation_token != ok2.activation_token  # unpredictable one-time secret


def test_oss_telemetry_is_derived_from_alarm_feed_and_otdr_from_topology():
    assert oss_adapter.get_tower_telemetry("TOWER-104")["status"] == "CRITICAL_ALARM"
    assert oss_adapter.get_tower_telemetry("TOWER-103")["status"] == "DEGRADED_WARNING"  # MINOR alarm
    assert oss_adapter.get_tower_telemetry("TOWER-101")["status"] == "OPERATIONAL"
    healthy = oss_adapter.run_otdr_fiber_test("OLT-HEALTHY-99/PON-01", "SUB-102948")
    assert healthy.line_status == "NORMAL_SYNC"


def test_minor_alarms_do_not_count_as_outage():
    gen = SyntheticDataGenerator()
    assert gen.get_active_alarms_for_sector("Sector-3")  # a MINOR alarm exists
    assert gen.get_active_alarms_for_sector("Sector-3", ("CRITICAL", "MAJOR")) == []


# --------------------------------------------------------------------------- agents / supervisor
def test_informational_billing_question_does_not_issue_a_credit():
    res = supervisor_agent.process_turn("Why is my bill so high this month?", "SUB-882194")
    assert "Adjustment ID" not in res.final_response and "credit of" not in res.final_response
    assert res.guardrail_passed and not res.escalated_to_human


def test_credit_above_autonomous_limit_needs_supervisor_not_self_approval():
    res = supervisor_agent.process_turn("I demand a credit of $75 for the inconvenience.", "SUB-882194")
    assert res.escalated_to_human and "supervisor" in res.final_response.lower()
    assert "Adjustment ID" not in res.final_response


def test_stranded_esim_request_hits_the_same_antifraud_gate_as_sim_swap():
    res = supervisor_agent.process_turn("I'm stranded and lost my phone, I need a new eSIM QR code immediately!", "SUB-773019")
    assert "Anti-Fraud" in res.final_response and "SM-DP+" not in res.final_response


def test_unknown_subscriber_raises_instead_of_fabricating_a_profile():
    with pytest.raises(SubscriberNotFoundError):
        supervisor_agent.process_turn("hello", "SUB-NOPE")


def test_plan_orders_network_before_billing_and_retention_last():
    from src.datascience.churn_predictor import churn_predictor

    state = AgenticSessionState(session_id="s", trace_id="t", subscriber_id="SUB-882194", sanitized_utterance="x")
    intent = intent_classifier.classify("my internet is down and my invoice charged me twice, I want to cancel")
    churn = churn_predictor.predict(12, 2, 0.0, "overdue", -0.7, True)
    tasks = supervisor_agent._formulate_plan(state, intent, churn)
    agents = [t.target_agent for t in tasks]
    assert agents == sorted(set(agents), key=agents.index)  # one task per agent
    assert agents.index("NetworkDiagnosticAgent") < agents.index("BillingAgent") < agents.index("RetentionWorkerAgent")
    billing = next(t for t in tasks if t.target_agent == "BillingAgent")
    network = next(t for t in tasks if t.target_agent == "NetworkDiagnosticAgent")
    assert network.task_id in billing.dependencies


def test_open_bss_circuit_degrades_gracefully_instead_of_500(monkeypatch):
    broken = CircuitBreaker("bss_adapter", CircuitBreakerConfig(failure_threshold=1))
    broken.state, broken.last_failure_time = CircuitState.OPEN, time.time()
    monkeypatch.setattr(bss_adapter, "circuit", broken)

    res = supervisor_agent.process_turn("I was charged twice ($30). Please refund this!", "SUB-882194")
    assert "Adjustment ID" not in res.final_response
    assert "billing system" in res.final_response


def test_two_consecutive_tool_failures_trigger_handoff():
    state = AgenticSessionState(session_id="s", trace_id="t", subscriber_id="SUB-1")
    state.record_step("A", "act", tool_name="bss", tool_output={"success": False, "error": "boom"})
    assert state.consecutive_tool_failures == 1
    state.record_step("B", "act", tool_name="oss", tool_output={"success": False, "error": "boom"})
    assert state.consecutive_tool_failures == 2
    state.record_step("C", "think")  # a reasoning step must not reset the failure streak
    assert state.consecutive_tool_failures == 2


def test_identical_subgoal_reinvocation_is_detected_as_a_loop():
    state = AgenticSessionState(session_id="s", trace_id="t", subscriber_id="SUB-1")
    assert state.register_invocation("BillingAgent", "same goal") is False
    assert state.register_invocation("BillingAgent", "same goal") is True


def test_frustration_velocity_triggers_proactive_handoff():
    res = supervisor_agent.process_turn(
        "This is horrible, unacceptable and useless!", "SUB-102948", historical_sentiments=[0.6]
    )
    assert res.escalated_to_human and "Frustration velocity" in (res.escalation_reason or "")


def test_pii_sessions_stay_on_prem_even_when_complex():
    complex_clear = hybrid_router.route_inference("t", 100, 100, is_high_complexity=True)
    complex_pii = hybrid_router.route_inference("t", 100, 100, is_high_complexity=True, contains_pii=True)
    assert complex_clear.provider.value == "gcp_vertex_ai"
    assert complex_pii.provider.value == "openstack_onprem"


# --------------------------------------------------------------------------- HITL
def test_handoff_dossier_never_contains_raw_pii():
    res = supervisor_agent.process_turn(
        "Charge my card 4532 1234 5678 8910, my pin is 8492, I demand a credit of $150.", "SUB-882194"
    )
    assert res.escalated_to_human
    dossier = next(m for m in reversed(warm_handoff_manager.queue) if m.trace_id == res.trace_id)
    blob = dossier.model_dump_json()
    assert "4532" not in blob and "8492" not in blob


def test_resolution_persists_notes_and_rejects_double_resolve():
    state = AgenticSessionState(session_id="s", trace_id="t", subscriber_id="SUB-882194", sanitized_utterance="hi")
    manifest = warm_handoff_manager.create_manifest(state, "test reason")
    resolved = warm_handoff_manager.resolve_escalation(manifest.escalation_id, "called customer back")
    assert resolved.resolution_notes == "called customer back" and resolved.resolved_at
    with pytest.raises(EscalationAlreadyResolvedError):
        warm_handoff_manager.resolve_escalation(manifest.escalation_id, "again")
    assert warm_handoff_manager.resolve_escalation("ESC-UNKNOWN", "x") is None


def test_escalation_ids_do_not_collide():
    state = AgenticSessionState(session_id="s", trace_id="t", subscriber_id="SUB-1")
    ids = {warm_handoff_manager.create_manifest(state, "r").escalation_id for _ in range(200)}
    assert len(ids) == 200


# --------------------------------------------------------------------------- data plane / MLOps
def test_event_bus_is_bounded():
    bus = StreamProcessor(capacity=3)
    for i in range(10):
        bus.publish("t", "trc", "sess", "E", {"i": i})
    assert len(bus.message_buffer) == 3
    assert len({e.event_id for e in bus.message_buffer}) == 3


def test_self_healing_is_idempotent_and_reversible():
    engine = SelfHealingEngine()
    drift = DriftReport(
        data_drift_detected=False, psi_score=0, ks_statistic=0, ks_p_value=1, concept_drift_detected=True,
        semantic_centroid_drift=0.4, tool_drift_detected=True, tool_frequency_anomaly={"oss_adapter": 3.0},
        rag_drift_detected=False, current_rag_similarity_ratio=1.0, mitigation_triggered=True,
    )
    healthy = drift.model_copy(update={"concept_drift_detected": False, "tool_drift_detected": False})
    try:
        first = engine.evaluate_and_heal(drift)
        assert {a.action_type for a in first} == {"DYNAMIC_RAG_REBALANCE", "TOOL_RATE_LIMIT_ISOLATION"}
        assert engine.evaluate_and_heal(drift) == []                      # persisting drift: nothing re-applied
        restore = engine.evaluate_and_heal(healthy)
        assert [a.action_type for a in restore] == ["RAG_WEIGHT_RESTORE"]
        assert hybrid_retriever.dense_weight == 1.0
    finally:
        hybrid_retriever.set_dynamic_weights(1.0, 1.0, 1.0)


def test_metrics_declare_which_fields_are_reference_values():
    from src.metrics.engine import metrics_engine

    snap = metrics_engine.get_snapshot()
    assert snap.system.data_source in ("live", "reference_baseline")
    assert "csat_score" in snap.business.reference_fields
    assert snap.ml_nlp.data_source == "offline_benchmark_reference"
