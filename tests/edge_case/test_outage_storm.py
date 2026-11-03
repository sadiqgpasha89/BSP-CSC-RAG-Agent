"""Edge Case Test: Cascading Network Outage Storm Geofence Deflection (Section 10.1)."""

from src.agents.supervisor import supervisor_agent


def test_outage_storm_proactive_deflection():
    # Subscriber SUB-882194 is in Sector-7 which has active critical fiber cut alarm
    subscriber_id = "SUB-882194"
    query = "My 5G internet is completely dead right now!"

    response = supervisor_agent.process_turn(
        user_utterance=query,
        subscriber_id=subscriber_id,
    )

    # Proactive deflection broadcast before expensive tool reasoning
    assert "infrastructure disruption in your area" in response.final_response
    assert "Sector-7" in response.final_response
    assert "SMS" in response.final_response
    assert response.steps_executed == 1  # Deflected in 1 step!
