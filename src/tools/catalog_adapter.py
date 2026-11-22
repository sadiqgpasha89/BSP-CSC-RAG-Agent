"""Product catalogue / BSS adapter (sandbox): plans, add-ons, offers, eligibility, price quotes, order lifecycle changes."""

from typing import Any, Dict, List, Optional

from src.tools.common import AdapterError, NotFoundError, load_sandbox_json
from src.tools.crm_adapter import crm_adapter
from src.tools.ocs_adapter import ocs_adapter


class CatalogAdapter:
    # ------------------------------------------------------------- catalogue
    def _cat(self) -> Dict[str, Any]:
        return load_sandbox_json("catalog/plans.json", {"plans": [], "addons": [], "recharge_packs": [], "retention_offers": []})

    def list_plans(self, service_type: Optional[str] = None, billing: Optional[str] = None) -> Dict[str, Any]:
        plans = [p for p in self._cat()["plans"] if p.get("sellable")
                 and (service_type is None or p["service_type"] == service_type)
                 and (billing is None or p["billing"] == billing)]
        return {"plans": plans, "currency": self._cat().get("currency", "USD"), "record_id": "CATALOG"}

    def get_plan(self, plan_code: str) -> Dict[str, Any]:
        for p in self._cat()["plans"]:
            if p["plan_code"] == plan_code:
                return p
        raise AdapterError("UNKNOWN_PLAN", f"Plan '{plan_code}' is not in the catalogue.")

    def get_addon(self, addon_code: str) -> Dict[str, Any]:
        for a in self._cat()["addons"]:
            if a["addon_code"] == addon_code:
                return a
        raise AdapterError("UNKNOWN_ADDON", f"Add-on '{addon_code}' is not in the catalogue.")

    def list_addons(self, subscriber_id: str) -> Dict[str, Any]:
        svc = crm_adapter.primary_service(subscriber_id)
        plan = crm_adapter.current_plan(subscriber_id)
        acct = self._billing_of(subscriber_id)
        out = []
        for a in self._cat()["addons"]:
            if svc and svc["type"] in a["service_types"] and acct in a["billing"]:
                out.append({**a, "eligible": plan not in a.get("excluded_plans", []),
                            "ineligible_reason": a.get("reason_excluded") if plan in a.get("excluded_plans", []) else None})
        return {"addons": out, "record_id": "CATALOG"}

    def compare_plans(self, plan_codes: List[str]) -> Dict[str, Any]:
        if not 2 <= len(plan_codes) <= 4:
            raise AdapterError("BAD_COMPARISON", "Compare between 2 and 4 plans.")
        plans = [self.get_plan(c) for c in plan_codes]
        return {"plans": plans, "cheapest": min(plans, key=lambda p: p["monthly_fee_usd"])["plan_code"], "record_id": "CATALOG"}

    def list_recharge_packs(self) -> Dict[str, Any]:
        return {"packs": self._cat()["recharge_packs"], "record_id": "CATALOG"}

    def get_pack(self, pack_id: str) -> Dict[str, Any]:
        for p in self._cat()["recharge_packs"]:
            if p["pack_id"] == pack_id:
                return p
        raise AdapterError("UNKNOWN_PACK", f"Recharge pack '{pack_id}' does not exist.")

    def _billing_of(self, subscriber_id: str) -> str:
        return crm_adapter.get_profile(subscriber_id)["account_type"]

    # ------------------------------------------------------- recommendations
    def recommend(self, subscriber_id: str, goal: str = "value") -> Dict[str, Any]:
        """Rank sellable plans for the subscriber's service type by fit to *measured* usage, then price."""
        svc = crm_adapter.primary_service(subscriber_id)
        if not svc:
            raise AdapterError("NO_ACTIVE_SERVICE", "No active service to recommend for.")
        billing = self._billing_of(subscriber_id)
        usage = ocs_adapter.get_usage_summary(subscriber_id, 30)
        monthly_gb = usage["total_data_gb"]
        current = self.get_plan(svc["plan_code"])
        candidates = self.list_plans(svc["type"], billing)["plans"]
        ranked = []
        for p in candidates:
            allow = p["allowances"].get("data_gb") if svc["type"] == "mobile" else None
            fits = allow is None or monthly_gb <= allow * 0.9
            if svc["type"] == "mobile" and allow is not None and not fits:
                reason = f"Your last 30 days used about {monthly_gb:.0f}GB, above this plan's {allow}GB."
            else:
                reason = "Fits your measured usage." if svc["type"] == "mobile" else "Same service type."
            ranked.append({"plan": p, "fits_usage": fits, "reason": reason,
                           "monthly_delta_usd": round(p["monthly_fee_usd"] - current["monthly_fee_usd"], 2),
                           "is_current": p["plan_code"] == current["plan_code"]})
        fitting = [r for r in ranked if r["fits_usage"]]
        key = (lambda r: r["plan"]["monthly_fee_usd"]) if goal in ("value", "cheapest") else (lambda r: -r["plan"]["monthly_fee_usd"])
        top = sorted(fitting or ranked, key=key)
        return {"current_plan": current["plan_code"], "measured_monthly_data_gb": monthly_gb,
                "recommendations": top[:3], "basis": "30-day measured usage from the charging system", "record_id": "CATALOG+OCS"}

    def recommend_recharge(self, subscriber_id: str) -> Dict[str, Any]:
        usage = ocs_adapter.get_usage_summary(subscriber_id, 30)
        balance = ocs_adapter.get_balance(subscriber_id)
        if balance["account_type"] != "prepaid":
            raise AdapterError("NOT_PREPAID", "Recharge recommendations apply to prepaid lines.")
        need = usage["avg_daily_data_gb"] * 30
        packs = sorted(self._cat()["recharge_packs"], key=lambda p: p["amount_usd"])
        best = next((p for p in packs if p["data_gb"] >= need * (p["validity_days"] / 30)), packs[-1])
        return {"estimated_monthly_need_gb": round(need, 1), "recommended_pack": best, "all_packs": packs,
                "basis": "30-day measured usage", "record_id": "CATALOG+OCS"}

    # ---------------------------------------------------------- eligibility
    def check_eligibility(self, subscriber_id: str, target_type: str, code: str, operation: str = "activate") -> Dict[str, Any]:
        svc = crm_adapter.primary_service(subscriber_id)
        if not svc:
            raise AdapterError("NO_ACTIVE_SERVICE", "No active service on this account.")
        billing = self._billing_of(subscriber_id)
        reasons: List[str] = []
        warnings: List[str] = []
        price_delta = 0.0
        if target_type == "plan":
            plan = self.get_plan(code)
            current = self.get_plan(svc["plan_code"])
            if not plan.get("sellable"):
                reasons.append("This plan is no longer available for sale.")
            if plan["service_type"] != svc["type"]:
                reasons.append(f"This is a {plan['service_type']} plan but your active service is {svc['type']}.")
            if plan["billing"] != billing:
                reasons.append(f"This is a {plan['billing']} plan; your account is {billing}.")
            if plan["plan_code"] == current["plan_code"]:
                reasons.append("You are already on this plan.")
            if current.get("legacy"):
                warnings.append("Your current plan is a grandfathered legacy plan. Switching ends those terms and you cannot move back to it.")
            price_delta = round(plan["monthly_fee_usd"] - current["monthly_fee_usd"], 2)
            return {"eligible": not reasons, "reasons": reasons, "warnings": warnings, "target": plan, "price_delta_usd": price_delta,
                    "new_monthly_fee_usd": plan["monthly_fee_usd"], "effective": "next billing cycle", "record_id": f"ELIG-{code}"}
        if target_type == "addon":
            addon = self.get_addon(code)
            active = {a["addon_code"] for a in crm_adapter.get_services(subscriber_id)["addons"]}
            if operation == "activate":
                if svc["type"] not in addon["service_types"]:
                    reasons.append(f"This add-on is not available for {svc['type']} services.")
                if billing not in addon["billing"]:
                    reasons.append(f"This add-on is not available for {billing} accounts.")
                if svc["plan_code"] in addon.get("excluded_plans", []):
                    reasons.append(addon.get("reason_excluded", "Your plan does not support this add-on."))
                if code in active:
                    reasons.append("This add-on is already active.")
                price_delta = addon["price_usd"]
            else:
                if code not in active:
                    reasons.append("This add-on is not active on your account.")
                price_delta = -addon["price_usd"] if addon["period"] == "monthly" else 0.0
            return {"eligible": not reasons, "reasons": reasons, "warnings": warnings, "target": addon, "price_delta_usd": price_delta,
                    "price_period": addon["period"], "effective": "immediately", "record_id": f"ELIG-{code}"}
        raise AdapterError("BAD_TARGET", "target_type must be 'plan' or 'addon'.")

    # ---------------------------------------------------------------- changes
    def change_plan(self, subscriber_id: str, plan_code: str) -> Dict[str, Any]:
        elig = self.check_eligibility(subscriber_id, "plan", plan_code)
        if not elig["eligible"]:
            raise AdapterError("NOT_ELIGIBLE", " ".join(elig["reasons"]))
        svc = crm_adapter.primary_service(subscriber_id)
        res = crm_adapter.set_plan(subscriber_id, svc["type"], plan_code)
        return {**res, "new_monthly_fee_usd": elig["new_monthly_fee_usd"], "effective": elig["effective"],
                "change_ref": f"CHG-{subscriber_id[-6:]}-{plan_code[:8]}", "record_id": f"CHG-{subscriber_id[-6:]}-{plan_code[:8]}"}

    def change_addon(self, subscriber_id: str, addon_code: str, operation: str) -> Dict[str, Any]:
        if operation not in ("activate", "deactivate"):
            raise AdapterError("BAD_OPERATION", "operation must be 'activate' or 'deactivate'.")
        elig = self.check_eligibility(subscriber_id, "addon", addon_code, operation)
        if not elig["eligible"]:
            raise AdapterError("NOT_ELIGIBLE", " ".join(elig["reasons"]))
        res = crm_adapter.set_addon(subscriber_id, addon_code, operation == "activate")
        return {**res, "operation": operation, "change_ref": f"ADD-{subscriber_id[-6:]}-{addon_code[:8]}", "record_id": f"ADD-{subscriber_id[-6:]}-{addon_code[:8]}"}

    # ----------------------------------------------------- retention offers
    def list_offers(self, subscriber_id: str) -> Dict[str, Any]:
        sub_clv = self._clv(subscriber_id)
        billing = self._billing_of(subscriber_id)
        offers = [o for o in self._cat()["retention_offers"]
                  if billing in o["billing"] and sub_clv >= o["min_clv_usd"] and (o["max_clv_usd"] is None or sub_clv <= o["max_clv_usd"])]
        return {"offers": offers, "record_id": "CATALOG-OFFERS"}

    def apply_offer(self, subscriber_id: str, offer_id: str) -> Dict[str, Any]:
        eligible = {o["offer_id"]: o for o in self.list_offers(subscriber_id)["offers"]}
        if offer_id not in eligible:
            raise AdapterError("NOT_ELIGIBLE", "That offer is not available for this account.")
        return {"offer_id": offer_id, "title": eligible[offer_id]["title"], "value_usd": eligible[offer_id]["value_usd"],
                "offer_ref": f"OFR-{subscriber_id[-6:]}-{offer_id[-6:]}", "record_id": f"OFR-{subscriber_id[-6:]}-{offer_id[-6:]}"}

    @staticmethod
    def _clv(subscriber_id: str) -> float:
        from src.data_engineering.synthetic_data_generator import data_generator
        sub = data_generator.get_subscriber(subscriber_id)
        if not sub:
            raise NotFoundError("Account")
        return float(sub.get("clv_usd", 0.0))


catalog_adapter = CatalogAdapter()
