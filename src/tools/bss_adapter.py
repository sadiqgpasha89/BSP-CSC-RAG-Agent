"""Billing Support System (BSS) Enterprise Tool Adapter."""

import math
import threading
import uuid
from collections import OrderedDict
from typing import Any, Dict, Optional

from pydantic import BaseModel

from src.core.config import settings
from src.core.logging import get_logger
from src.core.security import AuthContext, CPNIScope, security_service
from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.circuit_breaker import CircuitBreaker

logger = get_logger(__name__)

IDEMPOTENCY_CACHE_SIZE = 5000


class BillingCreditResult(BaseModel):
    success: bool
    adjustment_id: Optional[str] = None
    credited_amount_usd: float
    new_balance_usd: float
    authorization_level: str  # "AUTONOMOUS", "SUPERVISOR_OVERRIDE", "ESCALATION_REQUIRED", "REJECTED"
    message: str


class BSSAdapter:
    """Connects to Billing Support Systems for invoice reconciliation, CDR analysis, and refunds."""

    def __init__(self):
        self.circuit = CircuitBreaker("bss_adapter")
        self._idempotent_results: "OrderedDict[str, BillingCreditResult]" = OrderedDict()
        self._lock = threading.Lock()

    def get_billing_overview(self, subscriber_id: str) -> Dict[str, Any]:
        """Fetch billing balance, unbilled usage, and past payment status."""
        def _fetch():
            sub = data_generator.get_subscriber(subscriber_id)
            if not sub:
                return {"error": f"Subscriber '{subscriber_id}' not found in BSS"}
            return {
                "subscriber_id": sub["subscriber_id"],
                "msisdn": sub["msisdn"],
                "plan_code": sub["plan_code"],
                "monthly_rate_usd": sub["monthly_rate_usd"],
                "billing_status": sub["billing_status"],
                "last_bill_amount_usd": sub["last_bill_amount_usd"],
                "unbilled_usage_usd": sub["unbilled_usage_usd"],
                "currency": "USD",
                "payment_method": "AutoPay - Visa ending in **** 4532",
            }
        return self.circuit.call(_fetch)

    def get_itemized_cdr(self, subscriber_id: str, billing_cycle: str = "current") -> Dict[str, Any]:
        """Query Call Detail Records (CDRs) for data roaming and voice usage."""
        def _fetch_cdr():
            sub = data_generator.get_subscriber(subscriber_id)
            if not sub:
                return {"error": f"Subscriber '{subscriber_id}' not found"}

            # Simulated CDR records
            cdrs = [
                {"timestamp": "2026-09-10T14:22:00Z", "service": "International Roaming Data (Frankfurt, DE)", "volume_mb": 512, "billed_charge_usd": 30.00, "status": "DISPUTED"},
                {"timestamp": "2026-09-08T09:15:00Z", "service": "Domestic 5G Data", "volume_mb": 4200, "billed_charge_usd": 0.00, "status": "INCLUDED_IN_PLAN"},
                {"timestamp": "2026-09-05T18:30:00Z", "service": "International Voice Call (London, UK)", "duration_min": 15, "billed_charge_usd": 8.50, "status": "BILLED"},
            ]
            disputed_total = round(sum(r["billed_charge_usd"] for r in cdrs if r["status"] == "DISPUTED"), 2)
            return {
                "subscriber_id": subscriber_id,
                "billing_cycle": billing_cycle,
                "itemized_records": cdrs,
                "disputed_charges_total_usd": disputed_total,
            }
        return self.circuit.call(_fetch_cdr)

    @staticmethod
    def _rejected(message: str, level: str = "REJECTED") -> BillingCreditResult:
        return BillingCreditResult(
            success=False,
            credited_amount_usd=0.0,
            new_balance_usd=0.0,
            authorization_level=level,
            message=message,
        )

    def apply_billing_credit(
        self,
        subscriber_id: str,
        amount_usd: float,
        reason: str,
        authorized_by: str = "billing_sub_agent",
        auth_context: Optional[AuthContext] = None,
        idempotency_key: Optional[str] = None,
    ) -> BillingCreditResult:
        """Apply a financial credit or refund subject to deterministic fiscal policy limits.

        * ``amount_usd`` must be a finite, strictly positive number (negative "credits" would otherwise *increase* the balance).
        * When an ``auth_context`` is supplied the ``FINANCIAL_CREDIT`` CPNI scope is enforced.
        * ``idempotency_key`` makes retries / repeated turns safe: the original result is replayed, no second credit.
        """
        if not isinstance(amount_usd, (int, float)) or not math.isfinite(amount_usd) or amount_usd <= 0:
            logger.warning("Rejected non-positive or non-finite credit amount", amount_usd=str(amount_usd))
            return self._rejected("Credit amount must be a positive, finite dollar value.")

        if auth_context is not None:
            authorized, deny_reason = security_service.validate_cpni_access(auth_context, CPNIScope.FINANCIAL_CREDIT)
            if not authorized:
                return self._rejected(f"Billing credit denied: {deny_reason}", level="REJECTED_UNAUTHORIZED")

        # Hard regulatory & business rule boundaries
        if amount_usd > settings.max_refund_supervisor_required:
            logger.warning("Refund request exceeds supervisor ceiling; mandating HITL escalation", amount_usd=amount_usd)
            return self._rejected(
                f"Financial credit request of ${amount_usd:.2f} exceeds automated threshold (${settings.max_refund_supervisor_required:.2f}). Mandating human supervisor escalation.",
                level="ESCALATION_REQUIRED",
            )

        if idempotency_key:
            with self._lock:
                prior = self._idempotent_results.get(idempotency_key)
            if prior is not None:
                logger.info("Replaying idempotent credit result", key=idempotency_key)
                return prior

        def _execute_credit():
            sub = data_generator.get_subscriber(subscriber_id)
            if not sub:
                return self._rejected(f"Subscriber '{subscriber_id}' not found in BSS; no credit issued.")

            current_unbilled = sub.get("unbilled_usage_usd", 0.0)
            new_balance = max(0.0, current_unbilled - amount_usd)
            auth_level = "AUTONOMOUS" if amount_usd <= settings.max_refund_autonomous else "SUPERVISOR_OVERRIDE"

            return BillingCreditResult(
                success=True,
                adjustment_id=f"ADJ-{uuid.uuid4().hex[:8].upper()}",
                credited_amount_usd=round(amount_usd, 2),
                new_balance_usd=round(new_balance, 2),
                authorization_level=auth_level,
                message=f"Successfully credited ${amount_usd:.2f} to account {subscriber_id}. Reason: {reason} (authorized by {authorized_by})",
            )

        result = self.circuit.call(_execute_credit)

        if idempotency_key and result.success:
            with self._lock:
                self._idempotent_results[idempotency_key] = result
                while len(self._idempotent_results) > IDEMPOTENCY_CACHE_SIZE:
                    self._idempotent_results.popitem(last=False)
        return result


bss_adapter = BSSAdapter()
