"""Integration Test: End-to-End Network Outage & OTDR Diagnostic Workflow."""

from src.agents.supervisor import supervisor_agent


def test_e2e_network_fiber_diagnostic_opens_a_technical_case_after_consent_and_confirmation():
    sub, sess = "SUB-449182", "it-net-1"   # fibre subscriber in an affected sector
    query = "My fiber broadband is down, the Broadband PON light is flashing red. Run an OTDR test please."
    r = supervisor_agent.process_turn(query, sub, session_id=sess)

    assert r.intent_detected == "NETWORK_OUTAGE_DIAGNOSTICS" and r.steps_executed >= 1
    assert "fibre break" in r.final_response and "technician" in r.final_response.lower()
    assert r.guardrail_passed

    r2 = supervisor_agent.process_turn("I agree", sub, session_id=sess)
    assert r2.pending_action and "technical case" in r2.final_response
    r3 = supervisor_agent.process_turn("yes", sub, session_id=sess)
    assert "TCK-" in r3.final_response
