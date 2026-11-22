"""Integration Test: End-to-End Multi-Agent Billing Dispute Workflow (evidence -> consent -> confirmation -> specialist case)."""

from src.agents.supervisor import supervisor_agent
from src.tools.ticketing_adapter import ticketing_adapter


def test_e2e_billing_dispute_registers_a_case_and_never_credits():
    sub, sess = "SUB-882194", "it-billing-1"
    query = "I was charged twice ($30) for roaming data in Frankfurt on my latest bill. Please refund this!"

    first = supervisor_agent.process_turn(query, sub, session_id=sess)
    assert first.intent_detected == "BILLING_DISPUTE" and first.guardrail_passed
    assert "permission" in first.final_response            # case processing needs the customer's consent first

    second = supervisor_agent.process_turn("I agree", sub, session_id=sess)
    assert second.pending_action and "Register a billing dispute" in second.final_response
    before = len(ticketing_adapter.list_cases(sub)["cases"])   # nothing happens before the customer confirms
    assert before == len(ticketing_adapter.list_cases(sub)["cases"])

    done = supervisor_agent.process_turn("yes", sub, session_id=sess)
    assert "registered as case TCK-" in done.final_response
    assert "No credit has been applied" in done.final_response
    assert "Adjustment ID" not in done.final_response
    assert done.escalated_to_human is False
    cases = ticketing_adapter.list_cases(sub)["cases"]
    assert len(cases) == before + 1
    case = next(c for c in cases if c["subtype"] == "billing_dispute")
    assert case["queue"] == "BILLING_SPECIALIST"
