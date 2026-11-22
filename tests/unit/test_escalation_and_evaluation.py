"""Unit Tests for Escalation Evaluator, LLM Evaluator and Self-Healing."""

from src.hitl.escalation_manager import escalation_evaluator
from src.llmops.evaluator import llmops_evaluator
from src.mlops.self_healing import self_healing_engine
from src.mlops.drift_detector import DriftReport


def test_escalation_evaluator_triggers():
    # 1. Low intent confidence
    esc, reason = escalation_evaluator.should_escalate(
        intent_confidence=0.75, rag_confidence=0.85, consecutive_tool_failures=0
    )
    assert esc is True
    assert "Intent confidence" in reason

    # 2. Low RAG confidence
    esc, reason = escalation_evaluator.should_escalate(
        intent_confidence=0.95, rag_confidence=0.60, consecutive_tool_failures=0
    )
    assert esc is True
    assert "RAG retrieval relevance" in reason

    # 3. Two consecutive tool failures
    esc, reason = escalation_evaluator.should_escalate(
        intent_confidence=0.95, rag_confidence=0.85, consecutive_tool_failures=2
    )
    assert esc is True
    assert "Two consecutive backend" in reason

    # 4. Excessive concession request
    esc, reason = escalation_evaluator.should_escalate(
        intent_confidence=0.95, rag_confidence=0.85, consecutive_tool_failures=0, requested_concession_usd=150.0
    )
    assert esc is True
    assert "concession" in reason

    # 5. Policy violation
    esc, reason = escalation_evaluator.should_escalate(
        intent_confidence=0.95, rag_confidence=0.85, consecutive_tool_failures=0, policy_violated=True
    )
    assert esc is True
    assert "guardrail" in reason

    # 6. Cyclic loop
    esc, reason = escalation_evaluator.should_escalate(
        intent_confidence=0.95, rag_confidence=0.85, consecutive_tool_failures=0, is_cyclic_loop=True
    )
    assert esc is True
    assert "cyclic" in reason

    # 7. Nominal operation
    esc, reason = escalation_evaluator.should_escalate(
        intent_confidence=0.95, rag_confidence=0.85, consecutive_tool_failures=0
    )
    assert esc is False
    assert reason is None


def test_llmops_evaluator_full():
    rep = llmops_evaluator.evaluate_turn(
        query="What is my tethering hotspot limit on the 5G Ultra Max plan?",
        response="Your 5G Ultra Max plan includes 50GB of high-speed mobile hotspot tethering per month.",
        retrieved_contexts=["5G Ultra Max includes 50GB high-speed mobile hotspot."],
        ground_truth_context="50GB high-speed mobile hotspot per month.",
        optimal_tool_count=1,
        actual_tool_count=1,
    )
    assert rep.passed_all_thresholds is True
    assert rep.agent_path_efficiency == 1.0


def test_self_healing_engine_full():
    report = DriftReport(
        data_drift_detected=True,
        psi_score=0.25,
        ks_statistic=0.45,
        ks_p_value=0.01,
        concept_drift_detected=True,
        semantic_centroid_drift=0.35,
        tool_drift_detected=True,
        tool_frequency_anomaly={"oss_adapter": 3.2},
        rag_drift_detected=True,
        current_rag_similarity_ratio=0.72,
        mitigation_triggered=True,
    )
    actions = self_healing_engine.evaluate_and_heal(report)
    assert len(actions) >= 1
    assert len(self_healing_engine.get_healing_history()) >= 1
