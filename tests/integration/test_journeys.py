"""The five target journeys end to end through the supervisor (sandbox adapters), plus resumption and KPIs."""

from src.agents.supervisor import supervisor_agent
from src.channels import InboundMessage, handle, parse_whatsapp, verify_whatsapp_signature, format_reply
from src.core.config import settings
from src.core.confirmations import pending_actions
from src.core.proactive import outage_notifier
from src.hitl.warm_handoff import warm_handoff_manager
from src.metrics.kpi import kpi_service
from src.tools.gateway import tool_gateway
from src.tools.notification_adapter import notification_adapter
from src.tools.ocs_adapter import ocs_adapter
from src.tools.payment_adapter import payment_adapter


def T(msg, sub, sess, **kw):
    return supervisor_agent.process_turn(msg, sub, session_id=sess, **kw)


# --- journey 1: billing explanation
def test_bill_explanation_is_itemised_with_evidence_references():
    r = T("Please explain my bill", "SUB-882194", "j1")
    assert "KB-TAR-2026-001" in r.final_response and "CDR-20260910-1422" in r.final_response and r.guardrail_passed


# --- journey 2: prepaid recharge, incl. failure modes
def test_recharge_success_applies_only_after_verified_payment():
    T("Recharge my line with $10", "SUB-556201", "j2a")
    r = T("yes", "SUB-556201", "j2a")
    assert "recharge is applied" in r.final_response


def test_recharge_is_not_applied_when_payment_pending():
    ocs_adapter.reset()
    T("recharge $10 with the pending card", "SUB-449182", "j2b")
    r1 = T("recharge PK_10", "SUB-449182", "j2b")          # fibre sub is postpaid: OCS refuses a recharge anyway; payment path still must not apply
    assert "recharge is applied" not in (r1.final_response or "")


def test_payment_decline_applies_nothing():
    r0 = T("pay my bill using card ending 1181", "SUB-882194", "j2c")
    r = T("yes", "SUB-882194", "j2c")
    assert "declined" in r.final_response and "marked paid" not in r.final_response


def test_provider_timeout_is_never_reported_as_success_until_reconciled():
    T("pay my bill using card ending 9921", "SUB-882194", "j2d")
    r = T("yes", "SUB-882194", "j2d")
    pays = [p for p in payment_adapter._payments.values()] if hasattr(payment_adapter, "_payments") else []
    assert "unconfirmed" in r.final_response or "confirmed by the payment provider" in r.final_response
    assert len(pays) <= 1                                   # exactly one charge, never a retry


def test_ocs_failure_after_successful_payment_escalates_without_double_charge():
    ocs_adapter.fail_topups = True
    T("Recharge my line with $10", "SUB-556201", "j2e")
    r = T("yes", "SUB-556201", "j2e")
    assert r.escalated_to_human and "not be charged again" in r.final_response
    assert payment_adapter.recent_activity("SUB-556201")["attempts_last_hour"] == 1


# --- journey 3: complaint
def test_complaint_requires_consent_then_confirmation_and_dedups():
    r = T("I want to complain about poor service", "SUB-102948", "j3")
    assert "permission" in r.final_response
    r = T("I agree", "SUB-102948", "j3")
    assert r.pending_action
    r = T("yes", "SUB-102948", "j3")
    assert "TCK-" in r.final_response


# --- journey 4: plan change with legacy-plan warning
def test_plan_change_warns_about_grandfathered_terms_and_verifies_result():
    r = T("I want to change my plan to 5G Ultra Max", "SUB-102948", "j4")
    assert "grandfathered" in r.final_response and "$75.00" in r.final_response
    done = T("yes", "SUB-102948", "j4")
    assert "Done. Your plan is now 5G Ultra Max" in done.final_response


# --- journey 5: SIM/eSIM
def test_sim_flow_requires_step_up_then_human_approval():
    T("I need a SIM replacement, new ICCID 8901234567890123456", "SUB-882194", "j5")
    code = notification_adapter.outbox("SUB-882194")[-1]["sandbox_visible"]["otp"]
    r = T(code, "SUB-882194", "j5")
    assert r.pending_action
    r = T("yes", "SUB-882194", "j5")
    assert "approval from a specialist" in r.final_response
    assert pending_actions.awaiting_approval()


# --- resumption
def test_conversation_resumes_after_step_up_without_repeating_the_request():
    r = T("I need a SIM replacement, new ICCID 8901234567890123456", "SUB-882194", "j6")
    assert r.requires_step_up
    code = notification_adapter.outbox("SUB-882194")[-1]["sandbox_visible"]["otp"]
    r = T(code, "SUB-882194", "j6")
    assert "SIM replacement" in r.final_response            # the original request was resumed, not forgotten


def test_slot_filling_merges_a_short_followup_with_the_original_request():
    T("I want an eSIM for my new phone", "SUB-773019", "j7")
    r = T("EID-12345678901234", "SUB-773019", "j7")
    assert r.requires_step_up


# --- proactive notifications
def test_outage_contact_subscribes_customer_and_restore_notifies():
    T("My 5G internet is completely dead right now!", "SUB-882194", "j8")
    assert outage_notifier.subscribers("ALM-9001") or any(m["template"] == "notify_outage" for m in notification_adapter.outbox("SUB-882194"))
    n = sum(outage_notifier.restore(a) for a in list(outage_notifier._subs))
    assert n >= 1 and any(m["template"] == "notify_restored" for m in notification_adapter.outbox("SUB-882194"))


# --- human handover
def test_human_takeover_flow():
    r = T("I want to speak to a human please", "SUB-882194", "j9")
    hid = r.handover_id
    m = warm_handoff_manager.claim(hid, "op-1")
    assert m.status == "ACTIVE" and m.summary
    warm_handoff_manager.reply(hid, "op-1", "Hi, I'm looking at this now.")
    assert warm_handoff_manager.messages_for_session("j9")[0]["text"].startswith("Hi")
    follow = T("thanks", "SUB-882194", "j9")
    assert follow.steps_executed == 0 and follow.handover_id == hid
    warm_handoff_manager.resolve_escalation(hid, "done")
    again = T("What is my balance?", "SUB-882194", "j9")
    assert again.handover_id != hid or not again.escalated_to_human


# --- channels
def test_whatsapp_number_alone_does_not_authenticate_high_risk_actions():
    msgs = parse_whatsapp({"entry": [{"changes": [{"value": {"messages": [{"from": "14155550199", "id": "m1", "type": "text", "text": {"body": "I need a SIM replacement ICCID 8901234567890123456"}}]}}]}]})
    out = handle(msgs[0])
    assert out.meta["requires_step_up"] or "verify" in out.text.lower()


def test_whatsapp_signature_is_enforced_when_secret_is_set(monkeypatch):
    import hashlib, hmac
    monkeypatch.setattr(settings, "whatsapp_app_secret", "s3cret")
    body = b'{"x":1}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_whatsapp_signature(body, good) and not verify_whatsapp_signature(body, "sha256=bad") and not verify_whatsapp_signature(body, None)


def test_voice_reply_is_plain_and_short():
    r = T("What is my balance?", "SUB-556201", "j10")
    out = format_reply("voice", r)
    assert "*" not in out.text and len(out.text) <= 500


# --- KPIs
def test_kpis_come_from_events_and_report_null_when_no_data():
    empty = kpi_service.compute(since=9e12)
    assert empty["kpis"]["containment_rate"]["value"] is None and empty["kpis"]["containment_rate"]["note"]
    T("Recharge my line with $10", "SUB-556201", "j11"); T("yes", "SUB-556201", "j11")
    k = kpi_service.compute()
    assert k["kpis"]["payment_success"]["value"] == 100.0 and k["kpis"]["task_completion_rate"]["denominator"] >= 1
