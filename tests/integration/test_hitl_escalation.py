"""Integration Test: End-to-End Human-In-The-Loop (HITL) Escalation Workflow."""

from src.agents.supervisor import supervisor_agent
from src.hitl.warm_handoff import warm_handoff_manager


def test_e2e_hitl_escalation_on_credit_demand_beyond_evidence():
    # A $150 credit demand is not backed by invoice evidence and credits are human-only decisions.
    response = supervisor_agent.process_turn("I demand a credit of $150 for the inconvenience.", "SUB-882194", session_id="it-hitl-1")
    assert response.escalated_to_human is True
    assert "can't issue a credit" in response.final_response
    manifest = warm_handoff_manager.get(response.handover_id)
    assert manifest.priority in ("P1", "P2") and manifest.summary and manifest.suggested_next_action


def test_customer_messages_after_handover_go_to_the_human_not_the_bot():
    sub, sess = "SUB-882194", "it-hitl-2"
    first = supervisor_agent.process_turn("I demand a credit of $150 for the inconvenience.", sub, session_id=sess)
    follow = supervisor_agent.process_turn("Hello? Is anyone there?", sub, session_id=sess)
    assert follow.handover_id == first.handover_id and follow.steps_executed == 0
    assert any(m["role"] == "customer" for m in warm_handoff_manager.get(first.handover_id).messages)
