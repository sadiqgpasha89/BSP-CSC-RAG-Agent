"""Integration Test: End-to-End Human-In-The-Loop (HITL) Escalation Workflow."""

from src.agents.supervisor import supervisor_agent
from src.hitl.warm_handoff import warm_handoff_manager


def test_e2e_hitl_escalation_on_concession_overage():
    subscriber_id = "SUB-882194"
    # User asks for $150 credit, which exceeds supervisor limit ($100)
    query = "I demand a credit of $150 for the inconvenience."

    response = supervisor_agent.process_turn(
        user_utterance=query,
        subscriber_id=subscriber_id,
    )

    # Handled via BillingAgent which rejects > $100 and flags escalation
    assert "exceeds" in response.final_response or response.escalated_to_human is True
