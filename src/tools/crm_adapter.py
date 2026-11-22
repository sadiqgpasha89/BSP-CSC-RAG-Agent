"""CRM adapter (sandbox): customer profile, registered services, service history, profile updates."""

import copy
import threading
import time
from typing import Any, Dict, List, Optional

from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.common import AdapterError, NotFoundError, mask_value

EDITABLE_FIELDS = {"email", "address", "name"}


class CRMAdapter:
    def __init__(self):
        self._lock = threading.RLock()
        self._overlay: Dict[str, Dict[str, Any]] = {}       # runtime mutations on top of the static catalogue
        self._history: Dict[str, List[Dict[str, Any]]] = {}

    # ---------------------------------------------------------------- helpers
    def _base(self, subscriber_id: str) -> Dict[str, Any]:
        sub = data_generator.get_subscriber(subscriber_id)
        if not sub:
            raise NotFoundError("Account")
        return sub

    def _state(self, subscriber_id: str) -> Dict[str, Any]:
        sub = self._base(subscriber_id)
        with self._lock:
            st = self._overlay.get(sub["subscriber_id"])
            if st is None:
                st = {
                    "services": copy.deepcopy(sub.get("services", [])),
                    "addons": copy.deepcopy(sub.get("addons", [])),
                    "email": sub.get("email"),
                    "name": sub.get("name"),
                    "address": None,
                }
                self._overlay[sub["subscriber_id"]] = st
            return st

    def _log(self, subscriber_id: str, kind: str, detail: str) -> None:
        with self._lock:
            self._history.setdefault(subscriber_id, []).append({"ts": time.time(), "kind": kind, "detail": detail})

    # ------------------------------------------------------------------ reads
    def get_profile(self, subscriber_id: str) -> Dict[str, Any]:
        sub = self._base(subscriber_id)
        st = self._state(subscriber_id)
        email = st.get("email") or ""
        masked_email = (email[:1] + "***@" + email.split("@")[-1]) if "@" in email else None
        return {
            "subscriber_id": sub["subscriber_id"],
            "name": st.get("name"),
            "msisdn_masked": mask_value(sub["msisdn"], 4),
            "account_type": sub.get("account_type", "postpaid"),
            "tier": sub.get("tier"),
            "tenure_months": sub.get("tenure_months"),
            "email_masked": masked_email,
            "language": sub.get("language", "en"),
            "contract_start_date": sub.get("contract_start_date"),
            "record_id": sub["subscriber_id"],
        }

    def get_services(self, subscriber_id: str) -> Dict[str, Any]:
        sub = self._base(subscriber_id)
        st = self._state(subscriber_id)
        return {
            "subscriber_id": sub["subscriber_id"],
            "services": copy.deepcopy(st["services"]),
            "addons": copy.deepcopy(st["addons"]),
            "record_id": sub["subscriber_id"],
        }

    def current_plan(self, subscriber_id: str, service_type: Optional[str] = None) -> Optional[str]:
        services = self._state(subscriber_id)["services"]
        for svc in services:
            if svc["status"] == "ACTIVE" and (service_type is None or svc["type"] == service_type):
                return svc["plan_code"]
        return None

    def primary_service(self, subscriber_id: str) -> Optional[Dict[str, Any]]:
        services = [s for s in self._state(subscriber_id)["services"] if s["status"] == "ACTIVE"]
        return copy.deepcopy(services[0]) if services else None

    def service_history(self, subscriber_id: str) -> Dict[str, Any]:
        sub = self._base(subscriber_id)
        with self._lock:
            events = list(self._history.get(sub["subscriber_id"], []))
        return {"recent_tickets": sub.get("recent_tickets", []), "events": events, "record_id": sub["subscriber_id"]}

    # ----------------------------------------------------------------- writes
    def set_plan(self, subscriber_id: str, service_type: str, plan_code: str, cid: str = "") -> Dict[str, Any]:
        st = self._state(subscriber_id)
        with self._lock:
            for svc in st["services"]:
                if svc["type"] == service_type and svc["status"] == "ACTIVE":
                    old = svc["plan_code"]
                    svc["plan_code"] = plan_code
                    self._log(subscriber_id, "PLAN_CHANGE", f"{old} -> {plan_code}")
                    return {"service_id": svc["service_id"], "previous_plan": old, "plan_code": plan_code}
        raise AdapterError("NO_ACTIVE_SERVICE", f"No active {service_type} service on this account.")

    def set_addon(self, subscriber_id: str, addon_code: str, active: bool) -> Dict[str, Any]:
        st = self._state(subscriber_id)
        with self._lock:
            present = any(a["addon_code"] == addon_code for a in st["addons"])
            if active and not present:
                st["addons"].append({"addon_code": addon_code, "activated_at": time.time()})
            elif not active and present:
                st["addons"] = [a for a in st["addons"] if a["addon_code"] != addon_code]
            self._log(subscriber_id, "ADDON_ACTIVATED" if active else "ADDON_DEACTIVATED", addon_code)
        return {"addon_code": addon_code, "active": active}

    def update_profile(self, subscriber_id: str, field: str, value: str) -> Dict[str, Any]:
        if field not in EDITABLE_FIELDS:
            raise AdapterError("FIELD_NOT_EDITABLE", f"'{field}' cannot be changed through the assistant.")
        if field == "email" and ("@" not in value or len(value) > 120):
            raise AdapterError("INVALID_VALUE", "That does not look like a valid email address.")
        st = self._state(subscriber_id)
        with self._lock:
            st[field] = value.strip()[:200]
            self._log(subscriber_id, "PROFILE_UPDATE", f"{field} updated")
        return {"field": field, "updated": True, "record_id": subscriber_id}

    def reset(self) -> None:
        with self._lock:
            self._overlay.clear()
            self._history.clear()


crm_adapter = CRMAdapter()
