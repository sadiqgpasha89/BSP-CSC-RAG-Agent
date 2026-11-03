"""Integration Test: End-to-End Network Outage & OTDR Diagnostic Workflow."""

from src.agents.supervisor import supervisor_agent


def test_e2e_network_fiber_diagnostic():
    subscriber_id = "SUB-449182"  # Fiber subscriber
    query = "My fiber broadband is down, the Broadband PON light is flashing red. Run an OTDR test please."

    response = supervisor_agent.process_turn(
        user_utterance=query,
        subscriber_id=subscriber_id,
    )

    assert response.intent_detected == "NETWORK_OUTAGE_DIAGNOSTICS"
    assert response.steps_executed >= 1
    assert "optical fiber break" in response.final_response or "OTDR" in response.final_response
    assert "technician" in response.final_response.lower()
