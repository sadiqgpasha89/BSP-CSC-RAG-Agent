"""Proactive outage notifications.

A customer who contacts us during an outage is subscribed to updates for that incident. Messages go out only through
the consent-aware notification adapter (purpose ``proactive_service_notices``) on a channel the customer has enabled,
once when subscribed, again if the estimate changes, and once when service is restored.
"""

import threading
from typing import Any, Dict, List, Optional

from src.core.audit import audit_log
from src.core.consent import consent_service
from src.tools.notification_adapter import notification_adapter

PURPOSE = "proactive_service_notices"


class OutageNotifier:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs: Dict[str, Dict[str, Any]] = {}      # alarm_id -> {"sector", "eta", "members": {subscriber_id: language}}

    def subscribe(self, subscriber_id: str, alarm_id: str, sector: str, eta: Optional[str], language: str = "en", correlation_id: str = "") -> Dict[str, Any]:
        allowed = consent_service.allowed_channels(subscriber_id, PURPOSE)
        if not allowed:
            return {"subscribed": False, "channel": None, "reason": "NO_CONSENT_OR_CHANNEL"}
        with self._lock:
            inc = self._subs.setdefault(alarm_id, {"sector": sector, "eta": eta, "members": {}})
            first = subscriber_id not in inc["members"]
            inc["members"][subscriber_id] = language
        receipt = {"delivered": False, "channel": allowed[0]}
        if first:
            receipt = notification_adapter.send(subscriber_id, PURPOSE, "notify_outage", {"sector": sector, "eta": eta or "being assessed"}, language=language)
            audit_log.append("PROACTIVE_NOTICE", correlation_id or alarm_id, "outage_subscribe", "SUBSCRIBED", subscriber_id=subscriber_id, details={"alarm_id": alarm_id})
        return {"subscribed": True, "channel": receipt.get("channel") or allowed[0], "reason": None}

    def restore(self, alarm_id: str, correlation_id: str = "") -> int:
        with self._lock:
            inc = self._subs.pop(alarm_id, None)
        if not inc:
            return 0
        sent = 0
        for sid, lang in inc["members"].items():
            r = notification_adapter.send(sid, PURPOSE, "notify_restored", {"sector": inc["sector"]}, language=lang)
            sent += 1 if r.get("delivered") else 0
        audit_log.append("PROACTIVE_NOTICE", correlation_id or alarm_id, "outage_restored", "SENT", details={"alarm_id": alarm_id, "sent": sent})
        return sent

    def subscribers(self, alarm_id: str) -> List[str]:
        with self._lock:
            return list(self._subs.get(alarm_id, {}).get("members", {}))

    def reset(self) -> None:
        with self._lock:
            self._subs.clear()


outage_notifier = OutageNotifier()
