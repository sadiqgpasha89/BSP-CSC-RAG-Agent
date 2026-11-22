"""Secure tool gateway: typed tool contracts + the single enforcement point between agents and enterprise systems.

Agents (and the LLM behind them) only *propose* tool calls. For every call the gateway, server-side and in this order:

1. resolves the tool contract (unknown tool -> denied);
2. refuses any attempt to address another customer (``subscriber_id`` / ``msisdn`` in arguments);
3. validates and coerces arguments against the tool's typed schema (extra fields forbidden);
4. checks the authenticated context belongs to this subscriber, is unexpired, and holds the CPNI-style scope;
5. asks the policy engine (least-privilege agent permissions, assurance level, limits, step-up, consent, confirmation,
   human approval);
6. for consequential actions, freezes the arguments into a single-use pending action and returns
   ``CONFIRMATION_REQUIRED`` - only ``confirm`` (the customer-facing path) can release it;
7. executes through a circuit breaker with a timeout, bounded retries for read-only tools, and an idempotency key;
8. audits every step (masked, hash-chained) and emits KPI events with a correlation id.

Nothing here trusts model output as authorisation.
"""

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.core.audit import audit_log
from src.core.config import settings
from src.core.confirmations import ActionStatus, PendingAction, idempotency_store, pending_actions
from src.core.consent import consent_service
from src.core.logging import get_logger
from src.core.policy import Effect, PolicyDecision, PolicyRequest, policy_engine
from src.core.security import AuthContext, CPNIScope, security_service
from src.data_engineering.stream_processor import stream_processor
from src.tools.bss_adapter import bss_adapter
from src.tools.catalog_adapter import catalog_adapter
from src.tools.circuit_breaker import CircuitBreaker, CircuitOpenError
from src.tools.common import AdapterError, NotFoundError, Provenance, ProviderTimeout, TransientError
from src.tools.crm_adapter import crm_adapter
from src.tools.notification_adapter import notification_adapter
from src.tools.ocs_adapter import ocs_adapter
from src.tools.order_adapter import order_adapter
from src.tools.oss_adapter import oss_adapter
from src.tools.payment_adapter import payment_adapter
from src.tools.provisioning_adapter import provisioning_adapter
from src.tools.ticketing_adapter import ticketing_adapter

logger = get_logger(__name__)
_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="tool")


# ------------------------------------------------------------------------------------------------ result types
class ToolStatus(str, Enum):
    OK = "OK"
    DENIED = "DENIED"
    INVALID_ARGS = "INVALID_ARGS"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    STEP_UP_REQUIRED = "STEP_UP_REQUIRED"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"      # customer confirmed; waiting for a human operator
    ESCALATE = "ESCALATE"
    NOT_FOUND = "NOT_FOUND"
    FAILED = "FAILED"                            # definitive failure: nothing changed
    UNAVAILABLE = "UNAVAILABLE"                  # backend down / circuit open: nothing known to have changed
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"          # effect may have happened: reconcile, never blindly retry


class ToolResult(BaseModel):
    status: ToolStatus
    tool: str
    data: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    error_code: Optional[str] = None
    pending_action_id: Optional[str] = None
    pending_summary: Optional[str] = None
    pending_expires_at: Optional[float] = None
    provenance: List[Provenance] = Field(default_factory=list)
    policy_reasons: List[str] = Field(default_factory=list)
    correlation_id: str = ""
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == ToolStatus.OK


class ToolContext(BaseModel):
    """Everything the gateway needs to know about who is acting; built server-side, never from model output."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    subscriber_id: str
    session_id: str
    trace_id: str
    agent: str
    auth_context: Optional[AuthContext] = None
    step_up_verified: bool = False
    channel: str = "web"
    language: str = "en"


# ------------------------------------------------------------------------------------------------ arg schemas
class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NoArgs(_Args):
    pass


class DaysArgs(_Args):
    days: int = Field(default=30, ge=1, le=30)


class InvoiceArgs(_Args):
    invoice_id: str = Field(default="latest", max_length=40, pattern=r"^[A-Za-z0-9\-_]+$")


class ListPlansArgs(_Args):
    service_type: Optional[Literal["mobile", "broadband"]] = None


class ComparePlansArgs(_Args):
    plan_codes: List[str] = Field(min_length=2, max_length=4)


class RecommendArgs(_Args):
    goal: Literal["value", "performance"] = "value"


class EligibilityArgs(_Args):
    target_type: Literal["plan", "addon"]
    code: str = Field(max_length=64, pattern=r"^[A-Z0-9_]+$")
    operation: Literal["activate", "deactivate"] = "activate"


class DiagArgs(_Args):
    service_type: Literal["mobile", "broadband"] = "mobile"


class OrderIdArgs(_Args):
    order_id: str = Field(max_length=32, pattern=r"^[A-Za-z0-9\-]+$")


class CaseIdArgs(_Args):
    case_id: str = Field(max_length=32, pattern=r"^[A-Za-z0-9\-]+$")


class PaymentIdArgs(_Args):
    payment_id: str = Field(max_length=32, pattern=r"^PAY-[A-Z0-9]+$")


class TicketCreateArgs(_Args):
    subtype: Literal["complaint", "technical", "service_request"]
    category: str = Field(max_length=24)
    summary: str = Field(min_length=3, max_length=1000)
    signals: Dict[str, bool] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)


class DisputeArgs(_Args):
    invoice_id: str = Field(max_length=40, pattern=r"^[A-Za-z0-9\-_]+$")
    line_ids: List[str] = Field(min_length=1, max_length=10)
    reason: str = Field(min_length=3, max_length=500)


class PaymentInitArgs(_Args):
    amount_usd: float = Field(gt=0, le=100000)
    method_id: str = Field(max_length=32, pattern=r"^[a-z0-9_]+$")
    purpose: Literal["RECHARGE", "BILL_PAYMENT"]
    reference: str = Field(max_length=60)
    allow_duplicate: bool = False


class RechargeApplyArgs(_Args):
    payment_id: str = Field(max_length=32, pattern=r"^PAY-[A-Z0-9]+$")
    pack_id: str = Field(max_length=16, pattern=r"^PK_[0-9]+$")


class SettleInvoiceArgs(_Args):
    payment_id: str = Field(max_length=32, pattern=r"^PAY-[A-Z0-9]+$")
    invoice_id: str = Field(max_length=40, pattern=r"^[A-Za-z0-9\-_]+$")


class PlanChangeArgs(_Args):
    plan_code: str = Field(max_length=64, pattern=r"^[A-Z0-9_]+$")


class AddonChangeArgs(_Args):
    addon_code: str = Field(max_length=64, pattern=r"^[A-Z0-9_]+$")
    operation: Literal["activate", "deactivate"]


class OfferApplyArgs(_Args):
    offer_id: str = Field(max_length=40, pattern=r"^[A-Z0-9_]+$")


class ProfileUpdateArgs(_Args):
    field: Literal["email", "address", "name"]
    value: str = Field(min_length=2, max_length=200)


class PreferencesArgs(_Args):
    channel: Optional[Literal["sms", "email", "whatsapp", "push"]] = None
    enabled: Optional[bool] = None
    purpose: Optional[Literal["case_processing", "marketing", "proactive_service_notices"]] = None
    granted: Optional[bool] = None


class SimReplaceArgs(_Args):
    iccid: str = Field(pattern=r"^89\d{16,18}$")
    reason: str = Field(min_length=3, max_length=200)


class EsimIssueArgs(_Args):
    device_eid: str = Field(pattern=r"^EID-[0-9]{10,20}$|^[0-9]{32}$")
    reason: str = Field(min_length=3, max_length=200)


class PortArgs(_Args):
    direction: Literal["in", "out"]
    reason: str = Field(min_length=3, max_length=200)


class NotifyArgs(_Args):
    template: Literal["notify_receipt", "notify_case_update"]
    data: Dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------------------------------------------------------ tool contracts
class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    action: str                                    # policy-pack action key
    system: str                                    # enterprise system (also the circuit-breaker name)
    args_model: Type[_Args]
    handler: Callable[[ToolContext, Any], Dict[str, Any]]
    scope: Optional[str] = None                    # CPNI-style scope (defence in depth beneath the policy engine)
    side_effect: bool = False
    amount_field: Optional[str] = None
    summarize: Optional[Callable[[ToolContext, Any], str]] = None
    record_key: str = "record_id"


def _method_label(ctx: ToolContext, method_id: str) -> str:
    try:
        for m in payment_adapter.list_methods(ctx.subscriber_id)["methods"]:
            if m["method_id"] == method_id:
                return m["label"]
    except AdapterError:
        pass
    return "the selected payment method"


def _build_registry() -> Dict[str, ToolSpec]:
    S = CPNIScope
    reg: List[ToolSpec] = [
        # ---- reads -----------------------------------------------------------------------------------------
        ToolSpec(name="crm.get_profile", description="Customer profile (masked).", action="account.read", system="CRM", args_model=NoArgs,
                 scope=S.VIEW_ACCOUNT, handler=lambda c, a: crm_adapter.get_profile(c.subscriber_id)),
        ToolSpec(name="crm.get_services", description="Registered services and add-ons.", action="account.read", system="CRM", args_model=NoArgs,
                 scope=S.VIEW_ACCOUNT, handler=lambda c, a: crm_adapter.get_services(c.subscriber_id)),
        ToolSpec(name="crm.get_history", description="Service history and recent cases.", action="account.read", system="CRM", args_model=NoArgs,
                 scope=S.VIEW_ACCOUNT, handler=lambda c, a: crm_adapter.service_history(c.subscriber_id)),
        ToolSpec(name="usage.get_balance", description="Prepaid balance or postpaid allowance/remaining usage.", action="usage.read", system="OCS",
                 args_model=NoArgs, scope=S.VIEW_ACCOUNT, handler=lambda c, a: ocs_adapter.get_balance(c.subscriber_id)),
        ToolSpec(name="usage.get_summary", description="Usage summary over N days.", action="usage.read", system="OCS", args_model=DaysArgs,
                 scope=S.VIEW_ACCOUNT, handler=lambda c, a: ocs_adapter.get_usage_summary(c.subscriber_id, a.days)),
        ToolSpec(name="usage.get_anomalies", description="Statistical usage anomalies.", action="usage.read", system="OCS", args_model=NoArgs,
                 scope=S.VIEW_ACCOUNT, handler=lambda c, a: ocs_adapter.get_anomalies(c.subscriber_id)),
        ToolSpec(name="billing.get_invoice", description="Invoice with itemised lines.", action="billing.read", system="BSS-Billing", args_model=InvoiceArgs,
                 scope=S.VIEW_BILLING, handler=lambda c, a: bss_adapter.get_invoice(c.subscriber_id, a.invoice_id)),
        ToolSpec(name="billing.explain_charges", description="Itemised explanation with evidence references.", action="billing.read", system="BSS-Billing",
                 args_model=InvoiceArgs, scope=S.VIEW_BILLING, handler=lambda c, a: bss_adapter.explain_invoice(c.subscriber_id, a.invoice_id)),
        ToolSpec(name="catalog.list_plans", description="Sellable plans.", action="catalog.read", system="BSS-Catalog", args_model=ListPlansArgs,
                 handler=lambda c, a: catalog_adapter.list_plans(a.service_type)),
        ToolSpec(name="catalog.compare_plans", description="Side-by-side plan comparison.", action="catalog.read", system="BSS-Catalog", args_model=ComparePlansArgs,
                 handler=lambda c, a: catalog_adapter.compare_plans(a.plan_codes)),
        ToolSpec(name="catalog.get_plan", description="One plan from the catalogue.", action="catalog.read", system="BSS-Catalog",
                 args_model=PlanChangeArgs, handler=lambda c, a: {"plan": catalog_adapter.get_plan(a.plan_code), "record_id": a.plan_code}),
        ToolSpec(name="catalog.list_addons", description="Add-ons with eligibility for this customer.", action="catalog.read", system="BSS-Catalog",
                 args_model=NoArgs, scope=S.VIEW_ACCOUNT, handler=lambda c, a: catalog_adapter.list_addons(c.subscriber_id)),
        ToolSpec(name="catalog.recommend", description="Plan recommendation from measured usage.", action="catalog.read", system="BSS-Catalog", args_model=RecommendArgs,
                 scope=S.VIEW_ACCOUNT, handler=lambda c, a: catalog_adapter.recommend(c.subscriber_id, a.goal)),
        ToolSpec(name="catalog.recommend_recharge", description="Prepaid recharge recommendation from measured usage.", action="catalog.read", system="BSS-Catalog",
                 args_model=NoArgs, scope=S.VIEW_ACCOUNT, handler=lambda c, a: catalog_adapter.recommend_recharge(c.subscriber_id)),
        ToolSpec(name="catalog.check_eligibility", description="Eligibility, warnings and price delta for a plan/add-on change.", action="catalog.read",
                 system="BSS-Catalog", args_model=EligibilityArgs, scope=S.VIEW_ACCOUNT,
                 handler=lambda c, a: catalog_adapter.check_eligibility(c.subscriber_id, a.target_type, a.code, a.operation)),
        ToolSpec(name="catalog.list_offers", description="Approved retention offers this customer is eligible for.", action="catalog.read", system="BSS-Catalog",
                 args_model=NoArgs, scope=S.VIEW_ACCOUNT, handler=lambda c, a: catalog_adapter.list_offers(c.subscriber_id)),
        ToolSpec(name="network.check_outage", description="Known outages for the customer's sector.", action="network.read", system="OSS", args_model=NoArgs,
                 scope=S.VIEW_ACCOUNT, handler=lambda c, a: _check_outage(c)),
        ToolSpec(name="network.run_diagnostics", description="Mobile/broadband diagnostics.", action="network.read", system="OSS", args_model=DiagArgs,
                 scope=S.VIEW_ACCOUNT, handler=lambda c, a: _run_diagnostics(c, a.service_type)),
        ToolSpec(name="order.list", description="Orders for this customer.", action="order.read", system="OMS", args_model=NoArgs, scope=S.VIEW_ORDERS,
                 handler=lambda c, a: order_adapter.list_orders(c.subscriber_id)),
        ToolSpec(name="order.get", description="One order with appointment.", action="order.read", system="OMS", args_model=OrderIdArgs, scope=S.VIEW_ORDERS,
                 handler=lambda c, a: order_adapter.get_order(c.subscriber_id, a.order_id)),
        ToolSpec(name="order.appointments", description="Appointments.", action="order.read", system="OMS", args_model=NoArgs, scope=S.VIEW_ORDERS,
                 handler=lambda c, a: order_adapter.get_appointments(c.subscriber_id)),
        ToolSpec(name="ticket.get", description="Case status.", action="ticket.read", system="Ticketing", args_model=CaseIdArgs, scope=S.VIEW_ACCOUNT,
                 handler=lambda c, a: ticketing_adapter.get_case(c.subscriber_id, a.case_id)),
        ToolSpec(name="ticket.list", description="Customer's cases.", action="ticket.read", system="Ticketing", args_model=NoArgs, scope=S.VIEW_ACCOUNT,
                 handler=lambda c, a: ticketing_adapter.list_cases(c.subscriber_id)),
        ToolSpec(name="payment.list_methods", description="Saved payment methods (opaque tokens + masked labels).", action="payment.read", system="Payment-Gateway",
                 args_model=NoArgs, scope=S.TXN_PAYMENT, handler=lambda c, a: payment_adapter.list_methods(c.subscriber_id)),
        ToolSpec(name="payment.get_status", description="Authoritative payment status.", action="payment.read", system="Payment-Gateway", args_model=PaymentIdArgs,
                 scope=S.TXN_PAYMENT, handler=lambda c, a: payment_adapter.get_status(c.subscriber_id, a.payment_id)),
        ToolSpec(name="payment.reconcile", description="Resolve a pending/unknown payment with the provider.", action="payment.reconcile", system="Payment-Gateway",
                 args_model=PaymentIdArgs, scope=S.TXN_PAYMENT, handler=lambda c, a: payment_adapter.reconcile(c.subscriber_id, a.payment_id)),
        # ---- consequential writes ----------------------------------------------------------------------------
        ToolSpec(name="ticket.create", description="Create a complaint / technical ticket / service request.", action="ticket.create", system="Ticketing",
                 args_model=TicketCreateArgs, scope=S.CASE_CREATE, side_effect=True,
                 summarize=lambda c, a: f"Register a {a.subtype.replace('_', ' ')} case: \"{a.summary[:140]}\"",
                 handler=lambda c, a: ticketing_adapter.create_case(c.subscriber_id, a.subtype, a.category, a.summary, context=a.context, signals=a.signals)),
        ToolSpec(name="billing.register_dispute", description="Register a billing dispute with evidence and route to a specialist.", action="billing.dispute",
                 system="BSS-Billing", args_model=DisputeArgs, scope=S.VIEW_BILLING, side_effect=True,
                 summarize=lambda c, a: f"Register a billing dispute on invoice {a.invoice_id} (lines {', '.join(a.line_ids)}) for specialist review",
                 handler=lambda c, a: bss_adapter.register_dispute(c.subscriber_id, a.invoice_id, a.line_ids, a.reason)),
        ToolSpec(name="payment.initiate", description="Charge a saved payment method.", action="payment.initiate", system="Payment-Gateway",
                 args_model=PaymentInitArgs, scope=S.TXN_PAYMENT, side_effect=True, amount_field="amount_usd",
                 summarize=lambda c, a: f"Pay ${a.amount_usd:.2f} using {_method_label(c, a.method_id)} ({a.purpose.replace('_', ' ').lower()}: {a.reference})",
                 handler=lambda c, a: payment_adapter.initiate(c.subscriber_id, a.amount_usd, a.method_id, a.purpose, a.reference,
                                                               idempotency_key=f"{c.session_id}:{a.method_id}:{a.amount_usd}:{a.reference}:{int(a.allow_duplicate)}",
                                                               allow_duplicate=a.allow_duplicate)),
        ToolSpec(name="recharge.apply", description="Apply a recharge pack after a VERIFIED successful payment.", action="recharge.apply", system="OCS",
                 args_model=RechargeApplyArgs, scope=S.TXN_PAYMENT, side_effect=True, handler=lambda c, a: _apply_recharge(c, a)),
        ToolSpec(name="invoice.settle", description="Apply a VERIFIED successful payment to an invoice.", action="invoice.settle", system="BSS-Billing",
                 args_model=SettleInvoiceArgs, scope=S.TXN_PAYMENT, side_effect=True, handler=lambda c, a: _settle_invoice(c, a)),
        ToolSpec(name="plan.change", description="Change the active plan.", action="plan.change", system="BSS-Catalog", args_model=PlanChangeArgs,
                 scope=S.TXN_PLAN, side_effect=True,
                 summarize=lambda c, a: _plan_summary(c, a),
                 handler=lambda c, a: _with_verify(c, catalog_adapter.change_plan(c.subscriber_id, a.plan_code), "plan", a.plan_code)),
        ToolSpec(name="addon.change", description="Activate or deactivate an add-on.", action="addon.change", system="BSS-Catalog", args_model=AddonChangeArgs,
                 scope=S.TXN_PLAN, side_effect=True,
                 summarize=lambda c, a: _addon_summary(c, a),
                 handler=lambda c, a: _with_verify(c, catalog_adapter.change_addon(c.subscriber_id, a.addon_code, a.operation), "addon", a.addon_code, a.operation)),
        ToolSpec(name="offer.apply", description="Apply a catalogue-approved retention offer.", action="offer.apply", system="BSS-Catalog", args_model=OfferApplyArgs,
                 scope=S.TXN_PLAN, side_effect=True, amount_field=None,
                 summarize=lambda c, a: f"Apply the retention offer {a.offer_id}",
                 handler=lambda c, a: catalog_adapter.apply_offer(c.subscriber_id, a.offer_id)),
        ToolSpec(name="profile.update", description="Update contact details.", action="profile.update", system="CRM", args_model=ProfileUpdateArgs,
                 scope=S.MODIFY_PROFILE, side_effect=True,
                 summarize=lambda c, a: f"Change your {a.field} on file",
                 handler=lambda c, a: crm_adapter.update_profile(c.subscriber_id, a.field, a.value)),
        ToolSpec(name="preferences.update", description="Update contact channels or consent.", action="preferences.update", system="CRM", args_model=PreferencesArgs,
                 scope=S.VIEW_ACCOUNT, side_effect=True,
                 summarize=lambda c, a: _prefs_summary(a),
                 handler=lambda c, a: _update_prefs(c, a)),
        ToolSpec(name="sim.replace", description="Request SIM replacement (human-approved).", action="sim.replace", system="Provisioning", args_model=SimReplaceArgs,
                 scope=S.HIGH_RISK_CREDENTIAL, side_effect=True, summarize=lambda c, a: "Request a SIM replacement for your line (a specialist must approve it)",
                 handler=lambda c, a: _sim_replace(c, a)),
        ToolSpec(name="esim.issue", description="Request eSIM profile (human-approved).", action="esim.issue", system="Provisioning", args_model=EsimIssueArgs,
                 scope=S.HIGH_RISK_CREDENTIAL, side_effect=True, summarize=lambda c, a: "Request an eSIM profile for your device (a specialist must approve it)",
                 handler=lambda c, a: _esim_issue(c, a)),
        ToolSpec(name="port.initiate", description="Start number portability (human-approved).", action="port.initiate", system="Provisioning", args_model=PortArgs,
                 scope=S.HIGH_RISK_CREDENTIAL, side_effect=True, summarize=lambda c, a: f"Start a number port-{a.direction} request (a specialist must approve it)",
                 handler=lambda c, a: {"port_request": f"PORT-{c.subscriber_id[-6:]}-{a.direction.upper()}", "status": "SUBMITTED_FOR_VALIDATION", "record_id": f"PORT-{c.subscriber_id[-6:]}-{a.direction.upper()}"}),
        ToolSpec(name="network.soft_reset", description="Subscriber-level signalling refresh (not an infrastructure change).", action="network.soft_reset", system="OSS",
                 args_model=NoArgs, scope=S.MODIFY_SERVICES, side_effect=True, summarize=lambda c, a: "Refresh your line's network registration (takes about a minute)",
                 handler=lambda c, a: oss_adapter.execute_core_soft_reset(msisdn=_msisdn(c)) | {"record_id": f"RESET-{c.subscriber_id[-6:]}"}),
        ToolSpec(name="notification.send", description="Send a transactional notification.", action="notification.send", system="Notifications", args_model=NotifyArgs,
                 scope=S.VIEW_ACCOUNT, side_effect=True,
                 handler=lambda c, a: notification_adapter.send(c.subscriber_id, "transaction_receipt" if a.template == "notify_receipt" else "case_update", a.template, a.data, language=c.language)),
    ]
    return {t.name: t for t in reg}


# ------------------------------------------------------------------------------------------------ handlers
def _msisdn(ctx: ToolContext) -> str:
    from src.data_engineering.synthetic_data_generator import data_generator
    return (data_generator.get_subscriber(ctx.subscriber_id) or {}).get("msisdn", "")


def _check_outage(ctx: ToolContext) -> Dict[str, Any]:
    from src.data_engineering.synthetic_data_generator import ACTIVE_OUTAGE_SEVERITIES, data_generator
    sub = data_generator.get_subscriber(ctx.subscriber_id)
    alarms = data_generator.get_active_alarms_for_sector(sub.get("assigned_sector", ""), ACTIVE_OUTAGE_SEVERITIES)
    return {"outage_confirmed": bool(alarms), "sector": sub.get("assigned_sector"), "alarms": [
        {"alarm_id": a["alarm_id"], "type": a.get("alarm_type"), "severity": a.get("severity"), "detected_at": a.get("detected_at"),
         "estimated_restoration_time": a.get("estimated_restoration_time"), "root_cause": a.get("root_cause"),
         "impacted_services": a.get("impacted_services"), "field_crew_dispatched": a.get("field_crew_dispatched")} for a in alarms],
        "record_id": alarms[0]["alarm_id"] if alarms else f"SECTOR-{sub.get('assigned_sector')}"}


def _run_diagnostics(ctx: ToolContext, service_type: str) -> Dict[str, Any]:
    from src.data_engineering.synthetic_data_generator import data_generator
    sub = data_generator.get_subscriber(ctx.subscriber_id)
    tower = oss_adapter.get_tower_telemetry(sub.get("current_tower_id"))
    out: Dict[str, Any] = {"service_type": service_type, "tower": {k: tower.get(k) for k in ("tower_id", "name", "status", "rf_power_state", "backhaul_latency_ms", "root_cause")},
                           "record_id": f"DIAG-{ctx.subscriber_id[-6:]}"}
    if service_type == "broadband":
        olt = sub.get("olt_port")
        if not olt:
            out["line"] = {"status": "NO_BROADBAND_SERVICE"}
        else:
            res = oss_adapter.run_otdr_fiber_test(olt_port=olt, subscriber_id=sub["subscriber_id"])
            out["line"] = {"status": res.line_status, "optical_power_dbm": res.optical_power_dbm, "fault_distance_meters": res.fault_distance_meters,
                           "technician_dispatch_needed": res.technician_dispatch_needed, "summary": res.diagnostic_summary}
    out["healthy"] = tower.get("status") == "OPERATIONAL" and out.get("line", {}).get("status", "NORMAL_SYNC") == "NORMAL_SYNC"
    return out


def _apply_recharge(ctx: ToolContext, a: RechargeApplyArgs) -> Dict[str, Any]:
    pay = payment_adapter.get_status(ctx.subscriber_id, a.payment_id)
    if pay["status"] != "SUCCESS":
        raise AdapterError("PAYMENT_NOT_VERIFIED", f"Payment is {pay['status']}, not SUCCESS; no recharge applied.")
    if pay["purpose"] != "RECHARGE":
        raise AdapterError("PAYMENT_PURPOSE_MISMATCH", "That payment was not made for a recharge.")
    receipt = ocs_adapter.topup(ctx.subscriber_id, pay["amount_usd"], a.pack_id, a.payment_id)
    payment_adapter.mark_applied(a.payment_id)
    return receipt


def _settle_invoice(ctx: ToolContext, a: SettleInvoiceArgs) -> Dict[str, Any]:
    pay = payment_adapter.get_status(ctx.subscriber_id, a.payment_id)
    if pay["purpose"] != "BILL_PAYMENT":
        raise AdapterError("PAYMENT_PURPOSE_MISMATCH", "That payment was not made for a bill.")
    res = bss_adapter.settle_invoice(ctx.subscriber_id, a.invoice_id, pay)
    payment_adapter.mark_applied(a.payment_id)
    return res


def _with_verify(ctx: ToolContext, result: Dict[str, Any], kind: str, code: str, operation: str = "activate") -> Dict[str, Any]:
    """Read the change back from the system of record before reporting it (never trust the write's own return)."""
    services = crm_adapter.get_services(ctx.subscriber_id)
    if kind == "plan":
        verified = any(s["plan_code"] == code for s in services["services"])
    else:
        present = any(a["addon_code"] == code for a in services["addons"])
        verified = present if operation == "activate" else not present
    return {**result, "verified_by_readback": verified}


def _plan_summary(ctx: ToolContext, a: PlanChangeArgs) -> str:
    elig = catalog_adapter.check_eligibility(ctx.subscriber_id, "plan", a.plan_code)
    t = elig["target"]
    s = f"Change your plan to {t['name']} at ${t['monthly_fee_usd']:.2f}/month (a change of {elig['price_delta_usd']:+.2f}), effective {elig['effective']}"
    if elig["warnings"]:
        s += ". Note: " + " ".join(elig["warnings"])
    return s


def _addon_summary(ctx: ToolContext, a: AddonChangeArgs) -> str:
    addon = catalog_adapter.get_addon(a.addon_code)
    verb = "Activate" if a.operation == "activate" else "Deactivate"
    return f"{verb} {addon['name']} (${addon['price_usd']:.2f} {addon['period'].replace('_', ' ')})"


def _prefs_summary(a: PreferencesArgs) -> str:
    parts = []
    if a.channel is not None:
        parts.append(f"{'enable' if a.enabled else 'disable'} {a.channel} messages")
    if a.purpose is not None:
        parts.append(f"{'allow' if a.granted else 'stop'} {a.purpose.replace('_', ' ')} messages")
    return "Update your contact preferences: " + ", ".join(parts) if parts else "Update your contact preferences"


def _update_prefs(ctx: ToolContext, a: PreferencesArgs) -> Dict[str, Any]:
    if a.channel is not None and a.enabled is not None:
        consent_service.set_channel(ctx.subscriber_id, a.channel, a.enabled, correlation_id=ctx.trace_id)
    if a.purpose is not None and a.granted is not None:
        consent_service.record_consent(ctx.subscriber_id, a.purpose, a.granted, correlation_id=ctx.trace_id)
    prefs = consent_service.get_preferences(ctx.subscriber_id)
    return {"channels": prefs.channels, "consents": {k: v.granted for k, v in prefs.consents.items()}, "record_id": f"PREFS-{ctx.subscriber_id}"}


def _sim_replace(ctx: ToolContext, a: SimReplaceArgs) -> Dict[str, Any]:
    res = provisioning_adapter.validate_sim_swap_request(ctx.subscriber_id, a.iccid, auth_context=ctx.auth_context)
    if not res.success:
        raise AdapterError("SIM_REPLACE_REJECTED", res.message)
    return {"request": f"SIMREQ-{ctx.subscriber_id[-6:]}", "status": "APPROVED_AND_QUEUED_FOR_ACTIVATION", "record_id": f"SIMREQ-{ctx.subscriber_id[-6:]}"}


def _esim_issue(ctx: ToolContext, a: EsimIssueArgs) -> Dict[str, Any]:
    res = provisioning_adapter.generate_esim_profile(ctx.subscriber_id, a.device_eid, auth_context=ctx.auth_context)
    if not res.success:
        raise AdapterError("ESIM_REJECTED", res.message)
    return {"status": "PROFILE_ISSUED", "activation_delivered_via": "secure_channel", "activation_token": res.activation_token, "record_id": f"ESIM-{ctx.subscriber_id[-6:]}"}


# ------------------------------------------------------------------------------------------------ the gateway
class _BusinessRefusal:
    """A definitive business-level refusal. Carried as a value so it does not count against the circuit breaker:
    a declined request is a healthy backend answering 'no', not an outage."""

    def __init__(self, error: AdapterError):
        self.error = error


def _business_errors_as_values(handler: Callable[[ToolContext, Any], Dict[str, Any]]) -> Callable[[ToolContext, Any], Any]:
    def _wrapped(ctx: ToolContext, args: Any) -> Any:
        try:
            return handler(ctx, args)
        except TransientError:
            raise
        except AdapterError as exc:
            return _BusinessRefusal(exc)
    return _wrapped


BLOCK_REASONS_UNSAFE = ("least privilege", "not permitted", "below the required", "cross-customer", "forbidden", "not defined")


class ToolGateway:
    def __init__(self):
        self.registry: Dict[str, ToolSpec] = _build_registry()
        self._breakers: Dict[str, CircuitBreaker] = {}

    # --------------------------------------------------------------- helpers
    def _breaker(self, system: str) -> CircuitBreaker:
        if system not in self._breakers:
            self._breakers[system] = CircuitBreaker(f"gw:{system}")
        return self._breakers[system]

    def tool_catalogue(self, agent: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        for spec in self.registry.values():
            if agent and not policy_engine.agent_may(agent, spec.action):
                continue
            out.append({"name": spec.name, "description": spec.description, "action": spec.action, "system": spec.system, "side_effect": spec.side_effect,
                        "args_schema": spec.args_model.model_json_schema()})
        return out

    def _emit(self, ctx: ToolContext, event: str, payload: Dict[str, Any]) -> None:
        stream_processor.publish("kpi_events", ctx.trace_id, ctx.session_id, event, {"subscriber_id": ctx.subscriber_id, **payload})

    def _audit(self, ctx: ToolContext, tool: str, event: str, outcome: str, details: Optional[Dict[str, Any]] = None) -> None:
        audit_log.append(event, ctx.trace_id, tool, outcome, session_id=ctx.session_id, subscriber_id=ctx.subscriber_id, actor=ctx.agent, details=details)

    def _result(self, ctx: ToolContext, spec_name: str, status: ToolStatus, started: float, **kw) -> ToolResult:
        res = ToolResult(status=status, tool=spec_name, correlation_id=ctx.trace_id, latency_ms=round((time.perf_counter() - started) * 1000, 2), **kw)
        return res

    def _policy_request(self, ctx: ToolContext, spec: ToolSpec, args: Optional[BaseModel], *, confirmed: bool, human_approved_by: Optional[str],
                        step_up_satisfied: bool = False) -> PolicyRequest:
        auth = ctx.auth_context
        valid = bool(auth and auth.subscriber_id == ctx.subscriber_id and time.time() <= auth.expires_at)
        amount = getattr(args, spec.amount_field) if (args is not None and spec.amount_field) else None
        if spec.action == "offer.apply" and args is not None:          # the value comes from the catalogue, never from the model
            offer = {o["offer_id"]: o for o in catalog_adapter.list_offers(ctx.subscriber_id)["offers"]}.get(args.offer_id)
            amount = float(offer["value_usd"]) if offer else None
        rule = policy_engine.action_rule(spec.action) or {}
        recent = {"attempts_last_hour": 0, "total_last_24h": 0.0}
        if spec.action == "payment.initiate":
            recent = payment_adapter.recent_activity(ctx.subscriber_id)
        consent_purpose = rule.get("consent")
        return PolicyRequest(
            action=spec.action, agent=ctx.agent, subscriber_id=ctx.subscriber_id,
            auth_level=auth.auth_level if valid else "unauthenticated",
            is_mfa_verified=bool(valid and auth.is_mfa_verified), step_up_verified=ctx.step_up_verified or step_up_satisfied,
            amount=amount, confirmed=confirmed, human_approved_by=human_approved_by,
            consent_granted=consent_service.has_consent(ctx.subscriber_id, consent_purpose) if consent_purpose else None,
            recent_total_amount=recent["total_last_24h"], recent_attempts=recent["attempts_last_hour"],
        )

    # --------------------------------------------------------------- invoke
    def invoke(self, ctx: ToolContext, tool: str, raw_args: Optional[Dict[str, Any]] = None) -> ToolResult:
        started = time.perf_counter()
        raw_args = dict(raw_args or {})
        spec = self.registry.get(tool)
        if spec is None:
            self._audit(ctx, tool, "TOOL_CALL", "DENIED_UNKNOWN_TOOL")
            self._emit(ctx, "UNSAFE_ACTION_BLOCKED", {"tool": tool, "agent": ctx.agent, "reason": "unknown_tool"})
            return self._result(ctx, tool, ToolStatus.DENIED, started, message="Unknown tool.", error_code="UNKNOWN_TOOL")

        # Cross-customer access: the model must never address a different subscriber / number.
        for forbidden in ("subscriber_id", "msisdn", "account_id", "customer_id"):
            if forbidden in raw_args:
                self._audit(ctx, tool, "TOOL_CALL", "DENIED_CROSS_CUSTOMER", {"attempted_field": forbidden})
                self._emit(ctx, "UNSAFE_ACTION_BLOCKED", {"tool": tool, "agent": ctx.agent, "reason": "cross_customer_argument"})
                return self._result(ctx, tool, ToolStatus.DENIED, started, error_code="CROSS_CUSTOMER_ACCESS",
                                    message="Tools act only on the authenticated customer's own account.")

        try:
            args = spec.args_model.model_validate(raw_args)
        except ValidationError as exc:
            fields = sorted({str(e["loc"][0]) if e["loc"] else "?" for e in exc.errors()})
            self._audit(ctx, tool, "TOOL_CALL", "INVALID_ARGS", {"fields": fields})
            return self._result(ctx, tool, ToolStatus.INVALID_ARGS, started, error_code="INVALID_ARGS",
                                message=f"Invalid or missing arguments: {', '.join(fields)}.")

        # Defence in depth: the authenticated context must belong to this subscriber and hold the scope.
        if spec.scope:
            auth = ctx.auth_context
            if auth is not None and auth.subscriber_id != ctx.subscriber_id:
                self._audit(ctx, tool, "TOOL_CALL", "DENIED_AUTH_SUBJECT_MISMATCH")
                self._emit(ctx, "UNSAFE_ACTION_BLOCKED", {"tool": tool, "agent": ctx.agent, "reason": "auth_subject_mismatch"})
                return self._result(ctx, tool, ToolStatus.DENIED, started, error_code="AUTH_SUBJECT_MISMATCH", message="Session does not belong to this account.")
            allowed, reason = security_service.validate_cpni_access(auth, spec.scope)
            if not allowed:
                needs_mfa = (policy_engine.action_rule(spec.action) or {}).get("min_auth") == "authenticated_mfa"
                if needs_mfa and auth is not None and auth.auth_level == "basic_pin" and not ctx.step_up_verified:
                    status = ToolStatus.STEP_UP_REQUIRED
                    reason = "This action needs an extra identity check (one-time code)."
                else:
                    status = ToolStatus.DENIED
                    self._emit(ctx, "UNSAFE_ACTION_BLOCKED", {"tool": tool, "agent": ctx.agent, "reason": "scope"})
                self._audit(ctx, tool, "POLICY_DECISION", f"SCOPE_{status.value}", {"reason": reason})
                return self._result(ctx, tool, status, started, error_code="SCOPE", message=reason, policy_reasons=[reason])

        decision = policy_engine.evaluate(self._policy_request(ctx, spec, args, confirmed=False, human_approved_by=None))
        self._audit(ctx, tool, "POLICY_DECISION", decision.effect.value, {"reasons": decision.reasons, "policy_version": decision.policy_version, "args": args.model_dump()})
        return self._act_on_decision(ctx, spec, args, decision, started)

    def _act_on_decision(self, ctx: ToolContext, spec: ToolSpec, args: BaseModel, decision: PolicyDecision, started: float) -> ToolResult:
        tool = spec.name
        reasons = decision.reasons
        e = decision.effect
        if e == Effect.DENY:
            if any(k in " ".join(reasons).lower() for k in BLOCK_REASONS_UNSAFE):
                self._emit(ctx, "UNSAFE_ACTION_BLOCKED", {"tool": tool, "agent": ctx.agent, "reason": "policy_deny"})
            return self._result(ctx, tool, ToolStatus.DENIED, started, error_code="POLICY_DENY", message=reasons[0] if reasons else "Denied by policy.", policy_reasons=reasons)
        if e == Effect.ESCALATE:
            return self._result(ctx, tool, ToolStatus.ESCALATE, started, error_code="POLICY_ESCALATE", message=reasons[0], policy_reasons=reasons)
        if e == Effect.REQUIRE_STEP_UP:
            return self._result(ctx, tool, ToolStatus.STEP_UP_REQUIRED, started, error_code="STEP_UP", message=reasons[0], policy_reasons=reasons)
        if e == Effect.REQUIRE_CONSENT:
            return self._result(ctx, tool, ToolStatus.CONSENT_REQUIRED, started, error_code="CONSENT", message=reasons[0], policy_reasons=reasons)
        if e in (Effect.REQUIRE_CONFIRMATION, Effect.REQUIRE_HUMAN_APPROVAL):
            rule = policy_engine.action_rule(spec.action) or {}
            requires = [r for r, flag in (("confirmation", rule.get("confirmation")), ("human_approval", rule.get("human_approval"))) if flag]
            summary = spec.summarize(ctx, args) if spec.summarize else f"Run {spec.name}"
            act = pending_actions.create(tool=tool, args=args.model_dump(), subscriber_id=ctx.subscriber_id, session_id=ctx.session_id, agent=ctx.agent,
                                         summary=summary, risk=decision.risk, requires=requires or ["human_approval"], correlation_id=ctx.trace_id)
            act.step_up_satisfied = act.step_up_satisfied or ctx.step_up_verified
            if "confirmation" not in requires:
                act.status = ActionStatus.AWAITING_APPROVAL
            self._audit(ctx, tool, "CONFIRMATION", "REQUESTED", {"action_id": act.action_id, "summary": summary, "expires_at": act.expires_at})
            status = ToolStatus.CONFIRMATION_REQUIRED if "confirmation" in requires else ToolStatus.APPROVAL_REQUIRED
            return self._result(ctx, tool, status, started, pending_action_id=act.action_id, pending_summary=summary, pending_expires_at=act.expires_at,
                                message=summary, policy_reasons=reasons)
        # ALLOW -> execute (idempotency key: stable hash of the call for reads; per-action for confirmed writes)
        key = f"call:{ctx.session_id}:{tool}:{hash(str(sorted(args.model_dump().items())))}" if not spec.side_effect else f"direct:{ctx.session_id}:{tool}:{id(args)}"
        return self._execute(ctx, spec, args, key, started)

    # --------------------------------------------------------------- confirm / cancel / approve
    def confirm(self, ctx: ToolContext, action_id: str) -> ToolResult:
        started = time.perf_counter()
        act = pending_actions.get(action_id)
        tool = act.tool if act else "unknown"
        # Single message for unknown / foreign / wrong-session ids: no oracle for other customers' action ids.
        if act is None or act.subscriber_id != ctx.subscriber_id or act.session_id != ctx.session_id:
            self._audit(ctx, tool, "CONFIRMATION", "REJECTED_NOT_FOUND_OR_FOREIGN", {"action_id": action_id})
            if act is not None:
                self._emit(ctx, "UNSAFE_ACTION_BLOCKED", {"tool": tool, "agent": ctx.agent, "reason": "foreign_confirmation"})
            return self._result(ctx, tool, ToolStatus.NOT_FOUND, started, error_code="ACTION_NOT_FOUND", message="That request was not found.")
        if act.status == ActionStatus.EXPIRED:
            self._audit(ctx, tool, "CONFIRMATION", "EXPIRED", {"action_id": action_id})
            return self._result(ctx, tool, ToolStatus.FAILED, started, error_code="EXPIRED", message="This request has expired. Nothing was changed.")
        if act.status in (ActionStatus.EXECUTED, ActionStatus.OUTCOME_UNKNOWN, ActionStatus.FAILED):
            replay = idempotency_store.begin(f"act:{act.action_id}")
            cached = replay[1]
            if cached:
                return ToolResult.model_validate({**cached, "message": cached.get("message", "")})
        if act.status != ActionStatus.PENDING_CONFIRMATION:
            return self._result(ctx, tool, ToolStatus.FAILED, started, error_code="NOT_PENDING", message=f"This request is already {act.status.value.lower()}.")

        spec = self.registry[act.tool]
        args = spec.args_model.model_validate(act.args)
        decision = policy_engine.evaluate(self._policy_request(ctx, spec, args, confirmed=True, human_approved_by=None, step_up_satisfied=act.step_up_satisfied))
        self._audit(ctx, tool, "POLICY_DECISION", decision.effect.value, {"phase": "confirm", "reasons": decision.reasons})
        if decision.effect == Effect.REQUIRE_STEP_UP:
            return self._result(ctx, tool, ToolStatus.STEP_UP_REQUIRED, started, error_code="STEP_UP", message=decision.reasons[0], pending_action_id=act.action_id)
        if decision.effect in (Effect.DENY, Effect.ESCALATE):
            act.status = ActionStatus.REJECTED
            return self._result(ctx, tool, ToolStatus.DENIED if decision.effect == Effect.DENY else ToolStatus.ESCALATE, started,
                                error_code="POLICY", message=decision.reasons[0], policy_reasons=decision.reasons)
        act.confirmed_at = time.time()
        act.step_up_satisfied = act.step_up_satisfied or ctx.step_up_verified
        self._audit(ctx, tool, "CONFIRMATION", "CONFIRMED_BY_CUSTOMER", {"action_id": act.action_id})
        if decision.effect == Effect.REQUIRE_HUMAN_APPROVAL:
            act.status = ActionStatus.AWAITING_APPROVAL
            self._audit(ctx, tool, "CONFIRMATION", "AWAITING_HUMAN_APPROVAL", {"action_id": act.action_id})
            return self._result(ctx, tool, ToolStatus.APPROVAL_REQUIRED, started, pending_action_id=act.action_id, pending_summary=act.summary,
                                message="Submitted for specialist approval.", policy_reasons=decision.reasons)
        res = self._execute(ctx, spec, args, f"act:{act.action_id}", started)
        act.status = {ToolStatus.OK: ActionStatus.EXECUTED, ToolStatus.OUTCOME_UNKNOWN: ActionStatus.OUTCOME_UNKNOWN}.get(res.status, ActionStatus.FAILED)
        act.result = res.model_dump(mode="json")
        return res

    def cancel(self, ctx: ToolContext, action_id: str) -> ToolResult:
        started = time.perf_counter()
        act = pending_actions.get(action_id)
        if act is None or act.subscriber_id != ctx.subscriber_id or act.session_id != ctx.session_id:
            return self._result(ctx, "unknown", ToolStatus.NOT_FOUND, started, error_code="ACTION_NOT_FOUND", message="That request was not found.")
        if act.status == ActionStatus.PENDING_CONFIRMATION:
            act.status = ActionStatus.CANCELLED
            self._audit(ctx, act.tool, "CONFIRMATION", "CANCELLED_BY_CUSTOMER", {"action_id": action_id})
        return self._result(ctx, act.tool, ToolStatus.OK, started, message="Cancelled.", pending_action_id=action_id)

    def approve(self, action_id: str, operator: str, approve: bool = True, note: str = "") -> ToolResult:
        """Operator path for human-approval actions (SIM/eSIM/port, over-limit credits). Never reachable from an agent."""
        started = time.perf_counter()
        act = pending_actions.get(action_id)
        if act is None or act.status != ActionStatus.AWAITING_APPROVAL:
            return ToolResult(status=ToolStatus.NOT_FOUND, tool="unknown", error_code="NOT_AWAITING_APPROVAL", message="No such request awaiting approval.", correlation_id=action_id)
        ctx = ToolContext(subscriber_id=act.subscriber_id, session_id=act.session_id, trace_id=act.correlation_id or action_id, agent=act.agent,
                          auth_context=security_service.issue_default_auth_context(act.subscriber_id, _msisdn_of(act.subscriber_id), "authenticated_mfa"),
                          step_up_verified=act.step_up_satisfied)
        if not approve:
            act.status = ActionStatus.REJECTED
            self._audit(ctx, act.tool, "CONFIRMATION", "REJECTED_BY_OPERATOR", {"action_id": action_id, "operator": operator, "note": note})
            return self._result(ctx, act.tool, ToolStatus.DENIED, started, error_code="REJECTED", message="Rejected by operator.", pending_action_id=action_id)
        spec = self.registry[act.tool]
        args = spec.args_model.model_validate(act.args)
        decision = policy_engine.evaluate(self._policy_request(ctx, spec, args, confirmed=True, human_approved_by=operator, step_up_satisfied=act.step_up_satisfied))
        self._audit(ctx, act.tool, "POLICY_DECISION", decision.effect.value, {"phase": "approve", "operator": operator, "reasons": decision.reasons})
        if not decision.allowed:
            act.status = ActionStatus.REJECTED
            return self._result(ctx, act.tool, ToolStatus.DENIED, started, error_code="POLICY", message=decision.reasons[0], policy_reasons=decision.reasons)
        act.approved_by, act.approved_at = operator, time.time()
        res = self._execute(ctx, spec, args, f"act:{act.action_id}", started)
        act.status = {ToolStatus.OK: ActionStatus.EXECUTED, ToolStatus.OUTCOME_UNKNOWN: ActionStatus.OUTCOME_UNKNOWN}.get(res.status, ActionStatus.FAILED)
        act.result = res.model_dump(mode="json")
        return res

    # --------------------------------------------------------------- execution
    def _execute(self, ctx: ToolContext, spec: ToolSpec, args: BaseModel, idem_key: str, started: float) -> ToolResult:
        state, prior = idempotency_store.begin(idem_key)
        if state == "replay" and prior is not None:
            self._audit(ctx, spec.name, "TOOL_CALL", "IDEMPOTENT_REPLAY", {"key": idem_key})
            return ToolResult.model_validate(prior)
        if state == "wait":
            prior = idempotency_store.wait(idem_key)
            if prior is not None:
                return ToolResult.model_validate(prior)
            return self._result(ctx, spec.name, ToolStatus.OUTCOME_UNKNOWN, started, error_code="IN_FLIGHT", message="The request is still being processed.")

        breaker = self._breaker(spec.system)
        attempts = 1 + (0 if spec.side_effect else settings.tool_max_retries)
        result: Optional[ToolResult] = None
        cache = True
        for attempt in range(attempts):
            try:
                fut = _pool.submit(lambda: breaker.call(_business_errors_as_values(spec.handler), ctx, args))
                data = fut.result(timeout=settings.tool_timeout_sec)
                if isinstance(data, _BusinessRefusal):
                    raise data.error
                prov = Provenance(system=spec.system, record_id=str(data.get(spec.record_key)) if isinstance(data, dict) and data.get(spec.record_key) else None,
                                  mock=settings.sandbox_mode)
                result = self._result(ctx, spec.name, ToolStatus.OK, started, data=data, provenance=[prov])
                break
            except NotFoundError as exc:
                result = self._result(ctx, spec.name, ToolStatus.NOT_FOUND, started, error_code=exc.code, message=exc.message)
                break
            except TransientError as exc:
                if attempt + 1 < attempts:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                result, cache = self._result(ctx, spec.name, ToolStatus.UNAVAILABLE, started, error_code=exc.code, message=exc.message), False
            except AdapterError as exc:
                result = self._result(ctx, spec.name, ToolStatus.FAILED, started, error_code=exc.code, message=exc.message)
                break
            except CircuitOpenError:
                result, cache = self._result(ctx, spec.name, ToolStatus.UNAVAILABLE, started, error_code="CIRCUIT_OPEN",
                                             message="That system is temporarily unavailable."), False
                break
            except ProviderTimeout as exc:
                # The effect may have happened. Never retry; surface the record id so the caller reconciles.
                result = self._result(ctx, spec.name, ToolStatus.OUTCOME_UNKNOWN, started, error_code="PROVIDER_TIMEOUT",
                                      data={"record_id": exc.record_id, "payment_id": exc.record_id}, message="The provider did not confirm the outcome.",
                                      provenance=[Provenance(system=spec.system, record_id=exc.record_id, mock=settings.sandbox_mode)])
                break
            except FuturesTimeout:
                if spec.side_effect:
                    result = self._result(ctx, spec.name, ToolStatus.OUTCOME_UNKNOWN, started, error_code="TIMEOUT", message="The system did not answer in time; the outcome is unknown.")
                    break
                if attempt + 1 < attempts:
                    continue
                result, cache = self._result(ctx, spec.name, ToolStatus.UNAVAILABLE, started, error_code="TIMEOUT", message="The system did not answer in time."), False
            except Exception as exc:  # last resort: never leak internals, never 500
                logger.error("Tool handler crashed", tool=spec.name, error=repr(exc))
                result, cache = self._result(ctx, spec.name, ToolStatus.FAILED, started, error_code="INTERNAL", message="Something went wrong on our side."), False
                break
        assert result is not None
        idempotency_store.finish(idem_key, result.model_dump(mode="json") if cache else None)
        self._audit(ctx, spec.name, "TOOL_CALL", result.status.value,
                    {"system": spec.system, "side_effect": spec.side_effect, "error_code": result.error_code, "latency_ms": result.latency_ms,
                     "refs": [p.record_id for p in result.provenance if p.record_id]})
        extra = {"payment_status": result.data.get("status"), "purpose": result.data.get("purpose")} if spec.name == "payment.initiate" and result.ok else {}
        if spec.name in ("plan.change", "addon.change") and result.ok:
            extra["verified"] = bool(result.data.get("verified_by_readback"))
        self._emit(ctx, "TOOL_CALL", {"tool": spec.name, "agent": ctx.agent, "status": result.status.value, "system": spec.system,
                                      "side_effect": spec.side_effect, "latency_ms": result.latency_ms, **extra})
        return result


def _msisdn_of(subscriber_id: str) -> str:
    from src.data_engineering.synthetic_data_generator import data_generator
    return (data_generator.get_subscriber(subscriber_id) or {}).get("msisdn", "")


tool_gateway = ToolGateway()
