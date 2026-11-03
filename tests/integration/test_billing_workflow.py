"""Integration Test: End-to-End Multi-Agent Billing Dispute Workflow."""

from src.agents.supervisor import supervisor_agent


def test_e2e_billing_dispute_resolution():
    subscriber_id = "SUB-882194"
    query = "I was charged twice ($30) for roaming data in Frankfurt on my latest bill. Please refund this!"

    response = supervisor_agent.process_turn(
        user_utterance=query,
        subscriber_id=subscriber_id,
    )

    assert response.intent_detected == "BILLING_DISPUTE"
    assert response.steps_executed >= 1
    assert response.guardrail_passed is True
    assert "credit of $30.00" in response.final_response
    assert "Adjustment ID" in response.final_response
    assert response.escalated_to_human is False
