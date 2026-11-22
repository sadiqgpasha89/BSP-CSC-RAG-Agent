"""OCS / charging adapter (sandbox): real-time prepaid balance, allowances, usage history, top-up."""

import statistics
import threading
from typing import Any, Dict, List

from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.common import AdapterError, NotFoundError, load_sandbox_json

ANOMALY_Z = 3.0
ANOMALY_MIN_RATIO = 2.0


class OCSAdapter:
    def __init__(self):
        self._lock = threading.RLock()
        self._runtime: Dict[str, Dict[str, Any]] = {}
        self._topups: Dict[str, Dict[str, Any]] = {}      # payment_id -> topup receipt (guards double crediting)
        self.fail_topups = False                          # sandbox fault injection: "paid but not activated" scenario

    def _usage(self, subscriber_id: str) -> Dict[str, Any]:
        sub = data_generator.get_subscriber(subscriber_id)
        if not sub:
            raise NotFoundError("Account")
        sid = sub["subscriber_id"]
        with self._lock:
            if sid not in self._runtime:
                data = load_sandbox_json("sandbox/usage.json", {}).get(sid)
                if data is None:
                    raise NotFoundError("Usage record")
                self._runtime[sid] = data
            return self._runtime[sid]

    @staticmethod
    def _remaining(allow: Any, used: Any) -> Any:
        return None if allow is None else round(max(0.0, allow - used), 2)

    def get_balance(self, subscriber_id: str) -> Dict[str, Any]:
        u = self._usage(subscriber_id)
        sid = data_generator.get_subscriber(subscriber_id)["subscriber_id"]
        out: Dict[str, Any] = {"subscriber_id": sid, "account_type": u["account_type"], "record_id": f"OCS-{sid}"}
        allow, used = u["allowances"], u["used"]
        if u["account_type"] == "prepaid":
            out.update({
                "balance_usd": u["balance_usd"], "balance_expires": u.get("balance_expires"),
                "active_pack": u.get("pack"),
                "data_gb": {"allowance": allow.get("data_gb"), "used": used["data_gb"], "remaining": self._remaining(allow.get("data_gb"), used["data_gb"])},
            })
        else:
            out.update({
                "cycle_resets": u["cycle_resets"], "unbilled_usage_usd": u.get("unbilled_usage_usd", 0.0),
                "data_gb": {"allowance": allow.get("data_gb"), "used": used["data_gb"], "remaining": self._remaining(allow.get("data_gb"), used["data_gb"])},
                "voice_min": {"allowance": allow.get("voice_min"), "used": used.get("voice_min", 0), "remaining": self._remaining(allow.get("voice_min"), used.get("voice_min", 0))},
                "sms": {"allowance": allow.get("sms"), "used": used.get("sms", 0), "remaining": self._remaining(allow.get("sms"), used.get("sms", 0))},
            })
        return out

    def get_usage_summary(self, subscriber_id: str, days: int = 30) -> Dict[str, Any]:
        u = self._usage(subscriber_id)
        hist: List[float] = u["daily_data_gb"][-max(1, min(days, 30)):]
        sid = data_generator.get_subscriber(subscriber_id)["subscriber_id"]
        return {
            "subscriber_id": sid, "days": len(hist), "total_data_gb": round(sum(hist), 2),
            "avg_daily_data_gb": round(sum(hist) / len(hist), 3), "peak_daily_data_gb": max(hist),
            "roaming_data_mb_this_cycle": u.get("roaming_data_mb_this_cycle", 0), "record_id": f"OCS-{sid}",
        }

    def get_anomalies(self, subscriber_id: str) -> Dict[str, Any]:
        """Flag days whose usage is a statistical outlier (z-score) AND materially (>=2x) above the baseline median."""
        u = self._usage(subscriber_id)
        hist = u["daily_data_gb"]
        sid = data_generator.get_subscriber(subscriber_id)["subscriber_id"]
        if len(hist) < 14:
            return {"subscriber_id": sid, "anomalies": [], "insufficient_history": True, "record_id": f"OCS-{sid}"}
        median = statistics.median(hist)
        stdev = statistics.pstdev(hist) or 1e-9
        mean = statistics.fmean(hist)
        anomalies = []
        for idx, val in enumerate(hist):
            z = (val - mean) / stdev
            if z >= ANOMALY_Z and median > 0 and val / median >= ANOMALY_MIN_RATIO:
                anomalies.append({"day_index": idx, "days_ago": len(hist) - 1 - idx, "data_gb": val,
                                  "baseline_median_gb": round(median, 3), "ratio": round(val / median, 1), "z_score": round(z, 1)})
        # The detector reports *statistics*; interpretation (roaming, device upgrade) stays with evidence.
        return {"subscriber_id": sid, "anomalies": anomalies, "insufficient_history": False, "record_id": f"OCS-{sid}"}

    def topup(self, subscriber_id: str, amount_usd: float, pack_id: str, payment_id: str) -> Dict[str, Any]:
        u = self._usage(subscriber_id)
        if u["account_type"] != "prepaid":
            raise AdapterError("NOT_PREPAID", "Recharge applies to prepaid lines only.")
        if self.fail_topups:
            raise AdapterError("OCS_UNAVAILABLE", "Charging system could not apply the recharge.")
        with self._lock:
            if payment_id in self._topups:
                return {**self._topups[payment_id], "replayed": True}
            packs = {p["pack_id"]: p for p in load_sandbox_json("catalog/plans.json", {}).get("recharge_packs", [])}
            pack = packs.get(pack_id)
            if pack is None or abs(pack["amount_usd"] - amount_usd) > 0.005:
                raise AdapterError("PACK_MISMATCH", "Recharge pack and paid amount do not match.")
            u["allowances"]["data_gb"] = round((u["allowances"].get("data_gb") or 0) + pack["data_gb"], 2)
            u["pack"] = {"pack_id": pack_id, "validity_days": pack["validity_days"]}
            receipt = {"topup_id": f"TOPUP-{payment_id[-8:].upper()}", "pack_id": pack_id, "data_gb_added": pack["data_gb"],
                       "validity_days": pack["validity_days"], "new_data_allowance_gb": u["allowances"]["data_gb"], "replayed": False,
                       "record_id": f"TOPUP-{payment_id[-8:].upper()}"}
            self._topups[payment_id] = receipt
            return receipt

    def reset(self) -> None:
        with self._lock:
            self._runtime.clear()
            self._topups.clear()
            self.fail_topups = False


ocs_adapter = OCSAdapter()
