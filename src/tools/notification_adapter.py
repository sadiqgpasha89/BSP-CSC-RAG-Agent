"""Notification adapter (sandbox outbox): SMS, email, WhatsApp, push. Consent-aware; localised templates."""

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from src.core.config import settings
from src.core.consent import consent_service
from src.core.i18n import render
from src.tools.common import AdapterError

OUTBOX_MAX = 5000


class NotificationAdapter:
    def __init__(self):
        self._outbox: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def send(self, subscriber_id: str, purpose: str, template: str, data: Optional[Dict[str, Any]] = None, channel: Optional[str] = None,
             language: str = "en", sandbox_visible: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Deliver (to the sandbox outbox) only if the customer's channel preferences and consent allow it."""
        chans = [channel] if channel else consent_service.allowed_channels(subscriber_id, purpose)
        chans = [c for c in chans if consent_service.can_contact(subscriber_id, c, purpose)]
        if not chans:
            return {"delivered": False, "reason": "NO_CONSENT_OR_CHANNEL", "record_id": None}
        ch = chans[0]
        text = render(template, language, **(data or {}))
        receipt_id = f"MSG-{uuid.uuid4().hex[:10].upper()}"
        entry = {"message_id": receipt_id, "subscriber_id": subscriber_id, "channel": ch, "purpose": purpose, "template": template,
                 "text": text, "language": language, "ts": time.time()}
        if settings.sandbox_mode and sandbox_visible:
            entry["sandbox_visible"] = sandbox_visible      # e.g. the OTP, so demos/tests can read it; never audited or logged
        with self._lock:
            self._outbox.append(entry)
            del self._outbox[:-OUTBOX_MAX]
        return {"delivered": True, "channel": ch, "message_id": receipt_id, "record_id": receipt_id}

    def outbox(self, subscriber_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(m) for m in self._outbox if subscriber_id is None or m["subscriber_id"] == subscriber_id]

    def reset(self) -> None:
        with self._lock:
            self._outbox.clear()


notification_adapter = NotificationAdapter()
