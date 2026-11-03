"""Edge Case Test: Temporal Grounding & Deprecated Knowledge Traps (Section 10.6)."""

from src.agents.supervisor import supervisor_agent


def test_legacy_grandfathered_plan_isolation():
    # Elena Rostova (SUB-102948) is on FREEDOM_UNLIMITED_2018
    subscriber_id = "SUB-102948"
    query = "How much high-speed mobile hotspot tethering data is included on my plan?"

    response = supervisor_agent.process_turn(
        user_utterance=query,
        subscriber_id=subscriber_id,
    )

    # Must surface 10GB (2018 grandfathered rule), NOT 50GB (2026 rule)
    assert "10GB" in response.final_response
    assert "Freedom Unlimited" in response.final_response
    assert "50GB" not in response.final_response
