"""Order-management adapter (sandbox): new connections, devices, installations, appointments, provisioning status."""

from typing import Any, Dict, List

from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.common import NotFoundError, load_sandbox_json


class OrderAdapter:
    def _sid(self, subscriber_id: str) -> str:
        sub = data_generator.get_subscriber(subscriber_id)
        if not sub:
            raise NotFoundError("Account")
        return sub["subscriber_id"]

    def _doc(self) -> Dict[str, Any]:
        return load_sandbox_json("sandbox/orders.json", {"orders": [], "appointments": []})

    def list_orders(self, subscriber_id: str) -> Dict[str, Any]:
        sid = self._sid(subscriber_id)
        orders = [o for o in self._doc()["orders"] if o["subscriber_id"] == sid]
        return {"orders": [self._view(o) for o in orders], "record_id": f"ORDERS-{sid}"}

    def get_order(self, subscriber_id: str, order_id: str) -> Dict[str, Any]:
        sid = self._sid(subscriber_id)
        for o in self._doc()["orders"]:
            if o["order_id"] == order_id.strip().upper() and o["subscriber_id"] == sid:
                view = self._view(o)
                apt = next((a for a in self._doc()["appointments"] if a["appointment_id"] == o.get("appointment_id") and a["subscriber_id"] == sid), None)
                if apt:
                    view["appointment"] = apt
                return view
        raise NotFoundError("Order")

    def get_appointments(self, subscriber_id: str) -> Dict[str, Any]:
        sid = self._sid(subscriber_id)
        return {"appointments": [a for a in self._doc()["appointments"] if a["subscriber_id"] == sid], "record_id": f"APPTS-{sid}"}

    @staticmethod
    def _view(o: Dict[str, Any]) -> Dict[str, Any]:
        v = {k: val for k, val in o.items() if k != "subscriber_id"}
        v["record_id"] = o["order_id"]
        v["latest_stage"] = o["stages"][-1][0] if o.get("stages") else o["status"]
        return v


order_adapter = OrderAdapter()
