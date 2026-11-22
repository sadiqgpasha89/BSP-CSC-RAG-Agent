"""Ticketing / case-management adapter (sandbox): complaints, billing disputes, technical tickets, service requests."""

import threading
import time
from typing import Any, Dict, List, Optional

from src.core.policy import policy_engine
from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.common import AdapterError, NotFoundError

SUBTYPES = {"complaint", "billing_dispute", "technical", "service_request"}
QUEUES = {"billing_dispute": "BILLING_SPECIALIST", "technical": "TECH_SUPPORT_T2", "complaint": "CARE_RESOLUTION", "service_request": "CARE_GENERAL"}
STATUSES = ("OPEN", "IN_PROGRESS", "WAITING_CUSTOMER", "RESOLVED", "CLOSED")
DEDUP_WINDOW_SEC = 600


class TicketingAdapter:
    def __init__(self):
        self._lock = threading.RLock()
        self._cases: Dict[str, Dict[str, Any]] = {}
        self._seq = 10000
        self._seeded = False

    def _seed(self) -> None:
        if self._seeded:
            return
        for sub in data_generator.load_subscribers():
            for t in sub.get("recent_tickets", []):
                self._cases[t["ticket_id"]] = {
                    "case_id": t["ticket_id"], "subscriber_id": sub["subscriber_id"], "subtype": "complaint" if t["category"] != "network" else "technical",
                    "category": t["category"], "summary": t["summary"], "priority": "P3", "queue": "CARE_RESOLUTION", "status": t["status"].upper(),
                    "created_at": time.time() - 86400 * 60, "sla_due": None, "assigned_to": None, "history": [{"ts": time.time() - 86400 * 59, "event": "SEEDED"}],
                    "context": {}, "seeded": True}
        self._seeded = True

    def classify(self, category: str, signals: Dict[str, Any]) -> str:
        """Deterministic prioritisation from the policy pack's rules (not from customer value)."""
        if signals.get("outage_confirmed") or signals.get("service_down_over_24h"):
            return "P1"
        if signals.get("billing_dispute") or signals.get("repeat_contact") or signals.get("service_down"):
            return "P2"
        return "P3"

    def create_case(self, subscriber_id: str, subtype: str, category: str, summary: str, priority: Optional[str] = None,
                    context: Optional[Dict[str, Any]] = None, signals: Optional[Dict[str, Any]] = None, source: str = "assistant") -> Dict[str, Any]:
        sub = data_generator.get_subscriber(subscriber_id)
        if not sub:
            raise NotFoundError("Account")
        if subtype not in SUBTYPES:
            raise AdapterError("BAD_SUBTYPE", f"Unknown case type '{subtype}'.")
        if not summary or not summary.strip():
            raise AdapterError("EMPTY_SUMMARY", "A case needs a description.")
        categories = policy_engine.section("complaints", {}).get("categories", [])
        if categories and category not in categories:
            category = "other"
        sid = sub["subscriber_id"]
        with self._lock:
            self._seed()
            now = time.time()
            for c in self._cases.values():        # duplicate-submission guard: same customer, same text, recent
                if c["subscriber_id"] == sid and c["subtype"] == subtype and c["summary"] == summary.strip() and now - c["created_at"] < DEDUP_WINDOW_SEC:
                    return {**self._view(c), "deduplicated": True}
            self._seq += 1
            prio = priority or self.classify(category, signals or {})
            sla_h = policy_engine.section("complaints", {}).get("sla_hours", {}).get(prio, 72)
            case = {"case_id": f"TCK-{self._seq}", "subscriber_id": sid, "subtype": subtype, "category": category, "summary": summary.strip()[:1000],
                    "priority": prio, "queue": QUEUES[subtype], "status": "OPEN", "created_at": now, "sla_due": now + sla_h * 3600,
                    "sla_hours": sla_h, "assigned_to": None, "history": [{"ts": now, "event": f"CREATED via {source}"}],
                    "context": context or {}, "seeded": False}
            self._cases[case["case_id"]] = case
            return {**self._view(case), "deduplicated": False}

    @staticmethod
    def _view(c: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: v for k, v in c.items() if k not in {"seeded"}}
        out["record_id"] = c["case_id"]
        return out

    def get_case(self, subscriber_id: str, case_id: str) -> Dict[str, Any]:
        sub = data_generator.get_subscriber(subscriber_id)
        with self._lock:
            self._seed()
            c = self._cases.get(case_id.strip().upper())
            if not sub or c is None or c["subscriber_id"] != sub["subscriber_id"]:
                raise NotFoundError("Case")
            return self._view(c)

    def list_cases(self, subscriber_id: str, open_only: bool = False) -> Dict[str, Any]:
        sub = data_generator.get_subscriber(subscriber_id)
        if not sub:
            raise NotFoundError("Account")
        with self._lock:
            self._seed()
            rows = [self._view(c) for c in self._cases.values() if c["subscriber_id"] == sub["subscriber_id"]
                    and (not open_only or c["status"] not in ("RESOLVED", "CLOSED"))]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return {"cases": rows, "record_id": f"CASES-{sub['subscriber_id']}"}

    # operator-side (not exposed to agents): status transitions drive proactive notifications
    def update_status(self, case_id: str, status: str, note: str, operator: str) -> Dict[str, Any]:
        if status not in STATUSES:
            raise AdapterError("BAD_STATUS", f"Status must be one of {STATUSES}.")
        with self._lock:
            self._seed()
            c = self._cases.get(case_id)
            if c is None:
                raise NotFoundError("Case")
            c["status"] = status
            c["assigned_to"] = c["assigned_to"] or operator
            c["history"].append({"ts": time.time(), "event": f"STATUS {status}", "note": note[:300], "by": operator})
            return self._view(c)

    def count_recent(self, subscriber_id: str, days: int = 30) -> int:
        sub = data_generator.get_subscriber(subscriber_id)
        cutoff = time.time() - days * 86400
        with self._lock:
            self._seed()
            return sum(1 for c in self._cases.values() if sub and c["subscriber_id"] == sub["subscriber_id"] and c["created_at"] >= cutoff)

    def reset(self) -> None:
        with self._lock:
            self._cases.clear()
            self._seq = 10000
            self._seeded = False


ticketing_adapter = TicketingAdapter()
