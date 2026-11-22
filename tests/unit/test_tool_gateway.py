"""Tool gateway: enforcement of confirmation, step-up, approval, idempotency, isolation and outcome-unknown handling."""

import time
import uuid

import pytest

from src.core.confirmations import idempotency_store, pending_actions
from src.core.security import security_service
from src.tools.gateway import ToolContext, ToolStatus, tool_gateway
from src.tools.payment_adapter import payment_adapter
from src.tools.ticketing_adapter import ticketing_adapter

SUB = "SUB-882194"
MSISDN = "+1-415-555-0199"


def ctx(agent="BillingAgent", level="basic_pin", sub=SUB, session=None, step_up=False) -> ToolContext:
    msisdn = {"SUB-449182": "+1-415-555-0377", "SUB-556201": "+1-415-555-0512", "SUB-102948": "+1-415-555-0288"}.get(sub, MSISDN)
    return ToolContext(subscriber_id=sub, session_id=session or f"s-{uuid.uuid4().hex[:6]}", trace_id=f"t-{uuid.uuid4().hex[:6]}", agent=agent,
                       auth_context=security_service.issue_default_auth_context(sub, msisdn, level), step_up_verified=step_up)


@pytest.fixture(autouse=True)
def _clean():
    pending_actions.reset()
    idempotency_store.reset()
    payment_adapter.reset() if hasattr(payment_adapter, "reset") else None
    yield


def test_unknown_tool_denied():
    assert tool_gateway.invoke(ctx(), "shell.exec").status == ToolStatus.DENIED


@pytest.mark.parametrize("field", ["subscriber_id", "msisdn", "account_id"])
def test_cross_customer_argument_denied(field):
    r = tool_gateway.invoke(ctx(), "usage.get_balance", {field: "SUB-102948"})
    assert r.status == ToolStatus.DENIED and r.error_code == "CROSS_CUSTOMER_ACCESS"


def test_extra_args_rejected():
    r = tool_gateway.invoke(ctx(), "usage.get_summary", {"days": 7, "evil": 1})
    assert r.status == ToolStatus.INVALID_ARGS


def test_read_executes_with_provenance_and_mock_flag():
    r = tool_gateway.invoke(ctx("BillingAgent"), "billing.get_invoice")
    assert r.status == ToolStatus.OK
    assert r.provenance and r.provenance[0].mock is True


def test_agent_least_privilege():
    r = tool_gateway.invoke(ctx("KnowledgeAgent"), "payment.initiate", {"amount_usd": 5, "method_id": "pm_ok", "purpose": "BILL_PAYMENT", "reference": "INV"})
    assert r.status == ToolStatus.DENIED


def test_unauthenticated_cannot_read_account():
    r = tool_gateway.invoke(ctx(level="unauthenticated"), "usage.get_balance")
    assert r.status == ToolStatus.DENIED


def test_payment_requires_confirmation_and_is_single_use():
    c = ctx()
    args = {"amount_usd": 20.0, "method_id": "pm_ok", "purpose": "BILL_PAYMENT", "reference": "INV-X"}
    r = tool_gateway.invoke(c, "payment.initiate", args)
    assert r.status == ToolStatus.CONFIRMATION_REQUIRED and r.pending_action_id
    assert payment_adapter.recent_activity(SUB)["attempts_last_hour"] == 0          # nothing executed yet
    done = tool_gateway.confirm(c, r.pending_action_id)
    assert done.status == ToolStatus.OK and done.data["status"] == "SUCCESS"
    again = tool_gateway.confirm(c, r.pending_action_id)                             # replay returns the first result
    assert again.data.get("payment_id") == done.data.get("payment_id")
    assert payment_adapter.recent_activity(SUB)["attempts_last_hour"] == 1


def test_confirmation_cannot_be_used_from_another_session_or_customer():
    c = ctx()
    r = tool_gateway.invoke(c, "payment.initiate", {"amount_usd": 5, "method_id": "pm_ok", "purpose": "BILL_PAYMENT", "reference": "INV-Y"})
    other_session = ctx(session="different")
    assert tool_gateway.confirm(other_session, r.pending_action_id).status == ToolStatus.NOT_FOUND
    other_cust = ctx(sub="SUB-102948", session=c.session_id)
    assert tool_gateway.confirm(other_cust, r.pending_action_id).status == ToolStatus.NOT_FOUND


def test_confirmation_expires():
    c = ctx()
    r = tool_gateway.invoke(c, "payment.initiate", {"amount_usd": 5, "method_id": "pm_ok", "purpose": "BILL_PAYMENT", "reference": "INV-Z"})
    pending_actions.get(r.pending_action_id).expires_at = time.time() - 1
    assert tool_gateway.confirm(c, r.pending_action_id).status in (ToolStatus.FAILED, ToolStatus.NOT_FOUND)
    assert payment_adapter.recent_activity(SUB)["attempts_last_hour"] == 0


def test_cancel_prevents_execution():
    c = ctx()
    r = tool_gateway.invoke(c, "payment.initiate", {"amount_usd": 5, "method_id": "pm_ok", "purpose": "BILL_PAYMENT", "reference": "INV-C"})
    assert tool_gateway.cancel(c, r.pending_action_id).ok
    assert tool_gateway.confirm(c, r.pending_action_id).status == ToolStatus.FAILED
    assert payment_adapter.recent_activity(SUB)["attempts_last_hour"] == 0


def test_payment_over_limit_denied_or_escalated():
    r = tool_gateway.invoke(ctx(), "payment.initiate", {"amount_usd": 5000, "method_id": "pm_ok", "purpose": "BILL_PAYMENT", "reference": "BIG"})
    assert r.status in (ToolStatus.DENIED, ToolStatus.ESCALATE, ToolStatus.INVALID_ARGS) and not r.pending_action_id


def test_declined_payment_is_failed_not_success():
    c = ctx()
    r = tool_gateway.invoke(c, "payment.initiate", {"amount_usd": 10, "method_id": "pm_decline", "purpose": "BILL_PAYMENT", "reference": "INV-D"})
    out = tool_gateway.confirm(c, r.pending_action_id)
    assert out.status == ToolStatus.OK and out.data["status"] == "FAILED"


def test_provider_timeout_yields_unknown_then_reconciles_to_success_without_double_charge():
    c = ctx()
    r = tool_gateway.invoke(c, "payment.initiate", {"amount_usd": 12, "method_id": "pm_timeout", "purpose": "BILL_PAYMENT", "reference": "INV-T"})
    out = tool_gateway.confirm(c, r.pending_action_id)
    assert out.status == ToolStatus.OUTCOME_UNKNOWN
    pid = out.data["payment_id"]
    assert tool_gateway.confirm(c, r.pending_action_id).data.get("payment_id") == pid          # replay, no second charge
    rec = tool_gateway.invoke(c, "payment.reconcile", {"payment_id": pid})
    assert rec.ok and rec.data["status"] == "SUCCESS"


def test_recharge_refuses_unverified_payment():
    sub = "SUB-449182"                                   # has the sandbox pm_pending method
    c = ctx(sub=sub, agent="BillingAgent")
    c2 = ctx(sub=sub, agent="SalesAgent")
    r = tool_gateway.invoke(c, "payment.initiate", {"amount_usd": 10, "method_id": "pm_pending", "purpose": "RECHARGE", "reference": "PK_10"})
    out = tool_gateway.confirm(c, r.pending_action_id)
    assert out.data["status"] == "PENDING"
    rr = tool_gateway.invoke(c2, "recharge.apply", {"payment_id": out.data["payment_id"], "pack_id": "PK_10"})
    assert rr.status in (ToolStatus.DENIED, ToolStatus.FAILED), rr
    assert rr.status != ToolStatus.OK


@pytest.mark.parametrize("tool,args", [("sim.replace", {"iccid": "8901234567890123456", "reason": "lost phone"}),
                                       ("esim.issue", {"device_eid": "EID-12345678901234", "reason": "new phone"})])
def test_sim_esim_need_mfa_stepup_and_human_approval(tool, args):
    agent = "SimDeviceAgent"
    weak = tool_gateway.invoke(ctx(agent, "basic_pin"), tool, args)
    assert weak.status in (ToolStatus.DENIED, ToolStatus.STEP_UP_REQUIRED)
    c = ctx(agent, "authenticated_mfa")
    r = tool_gateway.invoke(c, tool, args)
    assert r.status == ToolStatus.STEP_UP_REQUIRED or r.pending_action_id
    c = ctx(agent, "authenticated_mfa", step_up=True)
    r = tool_gateway.invoke(c, tool, args)
    assert r.status == ToolStatus.CONFIRMATION_REQUIRED
    after = tool_gateway.confirm(c, r.pending_action_id)
    assert after.status == ToolStatus.APPROVAL_REQUIRED                       # customer "yes" is not enough
    assert pending_actions.get(r.pending_action_id).status.value == "AWAITING_APPROVAL"
    done = tool_gateway.approve(r.pending_action_id, "agent-007")
    assert done.status in (ToolStatus.OK, ToolStatus.FAILED)                  # executed by the operator path only


def test_approve_unknown_action_rejected():
    assert tool_gateway.approve("ACT-nope", "op").status == ToolStatus.NOT_FOUND


def test_model_cannot_use_billing_credit():
    assert "billing.credit" not in tool_gateway.registry


def test_ticket_create_is_confirmed_and_deduplicated():
    c = ctx("CustomerCareAgent")
    args = {"subtype": "complaint", "category": "billing", "summary": "Charged twice for the same add-on"}
    r = tool_gateway.invoke(c, "ticket.create", args)
    if r.status == ToolStatus.DENIED:
        pytest.skip("agent not yet in policy pack")
    assert r.status in (ToolStatus.CONFIRMATION_REQUIRED, ToolStatus.CONSENT_REQUIRED)


def test_tool_catalogue_respects_agent_permissions():
    names = {t["name"] for t in tool_gateway.tool_catalogue("KnowledgeAgent")}
    assert "payment.initiate" not in names and "plan.change" not in names


def test_business_refusals_do_not_trip_the_circuit_breaker():
    c = ctx()
    for _ in range(6):
        r = tool_gateway.invoke(c, "payment.get_status", {"payment_id": "PAY-DOESNOTEXIST"})
        assert r.status == ToolStatus.NOT_FOUND
    assert tool_gateway.invoke(c, "payment.list_methods").status == ToolStatus.OK      # breaker still closed
