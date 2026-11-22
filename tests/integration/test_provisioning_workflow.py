"""Integration Test: End-to-End eSIM Provisioning Workflow (details -> OTP step-up -> confirmation -> human approval)."""

from src.agents.supervisor import supervisor_agent
from src.core.confirmations import pending_actions
from src.tools.gateway import tool_gateway
from src.tools.notification_adapter import notification_adapter


def test_e2e_esim_needs_step_up_confirmation_and_human_approval():
    sub, sess = "SUB-773019", "it-esim-1"
    r = supervisor_agent.process_turn("I bought a new phone and need an eSIM profile download QR code.", sub, session_id=sess)
    assert r.intent_detected == "PROVISIONING_ESIM" and "EID" in r.final_response and not r.escalated_to_human

    r = supervisor_agent.process_turn("My EID is EID-12345678901234", sub, session_id=sess)
    assert r.requires_step_up and "one-time code" in r.final_response
    code = notification_adapter.outbox(sub)[-1]["sandbox_visible"]["otp"]

    r = supervisor_agent.process_turn(code, sub, session_id=sess)
    assert r.pending_action and "eSIM" in r.final_response

    r = supervisor_agent.process_turn("yes", sub, session_id=sess)
    assert "approval from a specialist" in r.final_response                 # the customer's yes alone does not issue a profile
    awaiting = [a for a in pending_actions.awaiting_approval() if a.session_id == sess]
    assert len(awaiting) == 1 and awaiting[0].tool == "esim.issue"

    done = tool_gateway.approve(awaiting[0].action_id, "operator-9")
    assert done.ok and done.data["status"] == "PROFILE_ISSUED"
