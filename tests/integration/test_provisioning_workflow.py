"""Integration Test: End-to-End eSIM Provisioning Workflow."""

from src.agents.supervisor import supervisor_agent


def test_e2e_esim_provisioning():
    subscriber_id = "SUB-773019"
    query = "I bought a new phone and need an eSIM profile download QR code."

    response = supervisor_agent.process_turn(
        user_utterance=query,
        subscriber_id=subscriber_id,
    )

    assert response.intent_detected == "PROVISIONING_ESIM"
    assert "eSIM" in response.final_response
    assert "SM-DP+" in response.final_response or "QR code" in response.final_response
    assert response.escalated_to_human is False
