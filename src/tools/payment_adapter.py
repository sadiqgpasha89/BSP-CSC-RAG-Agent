"""Payment gateway adapter (sandbox).

The platform never sees card data: it only handles opaque *method ids* (tokens) that the payment provider's hosted
fields issued. Sandbox methods behave deterministically so every outcome class can be tested:

* ``pm_ok``       settles immediately (SUCCESS)
* ``pm_decline``  FAILED (INSUFFICIENT_FUNDS)
* ``pm_pending``  PENDING until it has been polled twice (bank-debit style settlement)
* ``pm_timeout``  the provider *charges* but the response is lost -> ProviderTimeout; the true outcome is UNKNOWN to the
                  caller until ``reconcile`` (the dangerous case: retrying would double-charge)
"""

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from src.core.config import settings
from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.common import AdapterError, NotFoundError, ProviderTimeout, load_sandbox_json

TERMINAL = {"SUCCESS", "FAILED"}


class PaymentAdapter:
    def __init__(self):
        self._lock = threading.RLock()
        self._payments: Dict[str, Dict[str, Any]] = {}
        self._by_idem: Dict[str, str] = {}

    # ---------------------------------------------------------------- methods
    def list_methods(self, subscriber_id: str) -> Dict[str, Any]:
        sub = data_generator.get_subscriber(subscriber_id)
        if not sub:
            raise NotFoundError("Account")
        methods = load_sandbox_json("sandbox/payment_methods.json", {}).get(sub["subscriber_id"], [])
        return {"methods": methods, "record_id": f"PM-{sub['subscriber_id']}"}

    def _method(self, subscriber_id: str, method_id: str) -> Dict[str, Any]:
        for m in self.list_methods(subscriber_id)["methods"]:
            if m["method_id"] == method_id:
                return m
        raise AdapterError("UNKNOWN_METHOD", "That payment method is not saved on this account.")

    # ---------------------------------------------------------------- initiate
    def initiate(self, subscriber_id: str, amount_usd: float, method_id: str, purpose: str, reference: str,
                 idempotency_key: str, allow_duplicate: bool = False) -> Dict[str, Any]:
        sub = data_generator.get_subscriber(subscriber_id)
        if not sub:
            raise NotFoundError("Account")
        sid = sub["subscriber_id"]
        if amount_usd <= 0 or amount_usd > 100_000:
            raise AdapterError("BAD_AMOUNT", "Amount must be positive.")
        self._method(sid, method_id)
        with self._lock:
            prior = self._by_idem.get(idempotency_key)
            if prior:
                return {**self._view(self._payments[prior]), "replayed": True}
            now = time.time()
            if not allow_duplicate:
                for p in self._payments.values():
                    if (p["subscriber_id"] == sid and p["method_id"] == method_id and abs(p["amount_usd"] - amount_usd) < 0.005
                            and p["purpose"] == purpose and p["reference"] == reference and p["status"] in {"SUCCESS", "PENDING", "UNKNOWN"}
                            and now - p["created_at"] < settings.payment_duplicate_window_sec):
                        return {"duplicate_suspected": True, "existing_payment_id": p["payment_id"], "existing_status": p["status"],
                                "record_id": p["payment_id"], "status": "DUPLICATE_SUSPECTED"}
            payment_id = f"PAY-{uuid.uuid4().hex[:10].upper()}"
            rec = {"payment_id": payment_id, "subscriber_id": sid, "amount_usd": round(amount_usd, 2), "method_id": method_id, "purpose": purpose,
                   "reference": reference, "status": "PENDING", "failure_code": None, "created_at": now, "polls": 0,
                   "provider_ref": f"PRV-{uuid.uuid4().hex[:8].upper()}", "applied": False, "reconciled": False}
            self._payments[payment_id] = rec
            self._by_idem[idempotency_key] = payment_id
            if method_id == "pm_ok":
                rec["status"] = "SUCCESS"
            elif method_id == "pm_decline":
                rec["status"] = "FAILED"
                rec["failure_code"] = "INSUFFICIENT_FUNDS"
            elif method_id == "pm_timeout":
                rec["status"] = "UNKNOWN"          # provider-side truth is SUCCESS, but the caller never hears back
                rec["_provider_truth"] = "SUCCESS"
                raise ProviderTimeout(f"Payment provider timed out for {payment_id}", record_id=payment_id)
            return {**self._view(rec), "replayed": False}

    @staticmethod
    def _view(rec: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in rec.items() if not k.startswith("_") and k != "polls"} | {"record_id": rec["payment_id"]}

    # ------------------------------------------------------------ status / reconcile
    def _owned(self, subscriber_id: str, payment_id: str) -> Dict[str, Any]:
        sub = data_generator.get_subscriber(subscriber_id)
        rec = self._payments.get(payment_id)
        if not sub or rec is None or rec["subscriber_id"] != sub["subscriber_id"]:
            raise NotFoundError("Payment")
        return rec

    def get_status(self, subscriber_id: str, payment_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self._owned(subscriber_id, payment_id)
            if rec["status"] == "PENDING" and rec["method_id"] == "pm_pending":
                rec["polls"] += 1
                if rec["polls"] >= 2:
                    rec["status"] = "SUCCESS"
            return self._view(rec)

    def reconcile(self, subscriber_id: str, payment_id: str) -> Dict[str, Any]:
        """Ask the provider for the authoritative state of a PENDING / UNKNOWN payment."""
        with self._lock:
            rec = self._owned(subscriber_id, payment_id)
            if rec["status"] in ("UNKNOWN", "PENDING"):
                truth = rec.get("_provider_truth")
                if truth:
                    rec["status"] = truth
                elif rec["method_id"] == "pm_pending":
                    rec["status"] = "SUCCESS"
                rec["reconciled"] = rec["status"] in TERMINAL
            return self._view(rec)

    def mark_applied(self, payment_id: str) -> None:
        with self._lock:
            self._payments[payment_id]["applied"] = True

    def recent_activity(self, subscriber_id: str) -> Dict[str, float]:
        """Used by the policy layer: attempts in the last hour and successful/pending value in the last 24h."""
        sub = data_generator.get_subscriber(subscriber_id)
        now = time.time()
        total, attempts = 0.0, 0
        with self._lock:
            for p in self._payments.values():
                if sub and p["subscriber_id"] == sub["subscriber_id"]:
                    if now - p["created_at"] < 3600:
                        attempts += 1
                    if now - p["created_at"] < 86400 and p["status"] in {"SUCCESS", "PENDING", "UNKNOWN"}:
                        total += p["amount_usd"]
        return {"attempts_last_hour": attempts, "total_last_24h": total}

    def list_for_subscriber(self, subscriber_id: str) -> List[Dict[str, Any]]:
        sub = data_generator.get_subscriber(subscriber_id)
        with self._lock:
            return [self._view(p) for p in self._payments.values() if sub and p["subscriber_id"] == sub["subscriber_id"]]

    def reset(self) -> None:
        with self._lock:
            self._payments.clear()
            self._by_idem.clear()


payment_adapter = PaymentAdapter()
