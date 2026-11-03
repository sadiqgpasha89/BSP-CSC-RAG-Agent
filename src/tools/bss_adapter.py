"""Billing Support System (BSS) Enterprise Tool Adapter."""

from typing import Dict, List, Any, Optional
from pydantic import BaseModel
from src.core.config import settings
from src.core.logging import get_logger
from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.circuit_breaker import CircuitBreaker

logger = get_logger(__name__)


class BillingCreditResult(BaseModel):
    success: bool
    adjustment_id: Optional[str] = None
    credited_amount_usd: float
    new_balance_usd: float
    authorization_level: str  # "AUTONOMOUS", "SUPERVISOR_OVERRIDE", "ESCALATION_REQUIRED"
    message: str


class BSSAdapter:
    """Connects to Billing Support Systems for invoice reconciliation, CDR analysis, and refunds."""

    def __init__(self):
        self.circuit = CircuitBreaker("bss_adapter")

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
            return {
                "subscriber_id": subscriber_id,
                "billing_cycle": billing_cycle,
                "itemized_records": cdrs,
                "disputed_charges_total_usd": 30.00,
            }
        return self.circuit.call(_fetch_cdr)

    def apply_billing_credit(
        self,
        subscriber_id: str,
        amount_usd: float,
        reason: str,
        authorized_by: str = "billing_sub_agent",
    ) -> BillingCreditResult:
        """Apply a financial credit or refund subject to deterministic fiscal policy limits."""
        # Hard regulatory & business rule boundaries
        if amount_usd > settings.max_refund_supervisor_required:
            logger.warning("Refund request exceeds supervisor ceiling; mandating HITL escalation", amount_usd=amount_usd)
            return BillingCreditResult(
                success=False,
                credited_amount_usd=0.0,
                new_balance_usd=0.0,
                authorization_level="ESCALATION_REQUIRED",
                message=f"Financial credit request of ${amount_usd:.2f} exceeds automated threshold (${settings.max_refund_supervisor_required:.2f}). Mandating human supervisor escalation.",
            )

        def _execute_credit():
            sub = data_generator.get_subscriber(subscriber_id)
            current_unbilled = sub.get("unbilled_usage_usd", 0.0) if sub else 0.0
            new_balance = max(0.0, current_unbilled - amount_usd)

            auth_level = "AUTONOMOUS" if amount_usd <= settings.max_refund_autonomous else "SUPERVISOR_OVERRIDE"

            return BillingCreditResult(
                success=True,
                adjustment_id=f"ADJ-{hashlib.md5(f'{subscriber_id}:{amount_usd}:{time.time()}'.encode()).hexdigest()[:8].upper()}",
                credited_amount_usd=amount_usd,
                new_balance_usd=round(new_balance, 2),
                authorization_level=auth_level,
                message=f"Successfully credited ${amount_usd:.2f} to account {subscriber_id}. Reason: {reason}",
            )

        import hashlib, time
        return self.circuit.call(_execute_credit)


bss_adapter = BSSAdapter()
