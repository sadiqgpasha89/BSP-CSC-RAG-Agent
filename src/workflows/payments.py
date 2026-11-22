"""Payment completion workflow.

A payment is only *money moved*; a recharge or invoice settlement is only applied after the provider's authoritative
status is SUCCESS. States: SUCCESS -> apply; PENDING -> wait and re-check; FAILED -> nothing applied;
UNKNOWN (provider timeout) -> reconcile with the provider, never retry the charge blindly.
"""

from typing import Any, Dict, List, Optional, Tuple

from src.agents.billing_agent import payment_sentence
from src.tools.gateway import ToolContext, ToolResult, ToolStatus, tool_gateway


def _billing_ctx(ctx: ToolContext) -> ToolContext:
    return ctx.model_copy(update={"agent": "BillingAgent"})


def apply_verified_payment(ctx: ToolContext, payment: Dict[str, Any]) -> Tuple[List[str], Optional[str]]:
    """Apply a SUCCESS payment to its target. Returns (customer messages, escalation reason or None)."""
    c = _billing_ctx(ctx)
    msgs: List[str] = [payment_sentence(payment)]
    if payment.get("applied"):
        return msgs, None
    if payment["purpose"] == "RECHARGE":
        r = tool_gateway.invoke(c, "recharge.apply", {"payment_id": payment["payment_id"], "pack_id": payment["reference"]})
        if r.ok:
            d = r.data
            msgs.append(f"Your recharge is applied: {d['data_gb_added']} GB added, valid {d['validity_days']} days (receipt {d['topup_id']}).")
            return msgs, None
        msgs.append("Your payment went through, but the recharge could not be applied yet. You will not be charged again; a specialist will complete it with your payment reference.")
        return msgs, f"Payment {payment['payment_id']} succeeded but recharge was not applied ({r.error_code})"
    if payment["purpose"] == "BILL_PAYMENT":
        r = tool_gateway.invoke(c, "invoice.settle", {"payment_id": payment["payment_id"], "invoice_id": payment["reference"]})
        if r.ok:
            msgs.append(f"Invoice {r.data['invoice_id']} is now marked paid.")
            return msgs, None
        msgs.append("Your payment went through, but I couldn't mark the invoice paid yet. A specialist will reconcile it; you won't be charged twice.")
        return msgs, f"Payment {payment['payment_id']} succeeded but invoice settlement failed ({r.error_code})"
    return msgs, None


def complete_payment(ctx: ToolContext, result: ToolResult) -> Tuple[List[str], Optional[str], Optional[str]]:
    """After a confirmed ``payment.initiate``: returns (messages, escalation reason, payment_id to track)."""
    c = _billing_ctx(ctx)
    pay_id = (result.data or {}).get("payment_id")
    if result.status == ToolStatus.OUTCOME_UNKNOWN and pay_id:
        rec = tool_gateway.invoke(c, "payment.reconcile", {"payment_id": pay_id})
        if rec.ok:
            result = ToolResult(status=ToolStatus.OK, tool="payment.initiate", data=rec.data, correlation_id=result.correlation_id)
    if result.status == ToolStatus.OK:
        p = result.data
        if p["status"] == "SUCCESS":
            msgs, esc = apply_verified_payment(ctx, p)
            return msgs, esc, p["payment_id"]
        if p["status"] == "PENDING":
            return [payment_sentence(p), "Ask me for the payment status in a minute and I'll check again."], None, p["payment_id"]
        return [payment_sentence(p)], None, None
    if result.status == ToolStatus.OUTCOME_UNKNOWN:
        return (["The payment provider did not confirm whether the payment went through. I haven't applied anything and I won't charge you again. A specialist will reconcile it with the provider."],
                f"Payment outcome unknown ({pay_id or 'no reference'}); needs reconciliation", pay_id)
    if result.status == ToolStatus.ESCALATE:
        return ["This payment needs a specialist's review before it can go ahead."], result.message, None
    return [result.message or "The payment could not be started. Nothing was charged."], None, None
