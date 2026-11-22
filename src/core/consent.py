"""Consent and communication-preference management.

Consent is per-purpose (case processing, marketing, proactive service notices) and per-channel. Operational messages
(OTP, transaction receipts) are transactional and do not need marketing consent; marketing never goes out without an
explicit opt-in. Defaults come from the policy pack, not from code.
"""

import threading
import time
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from src.core.audit import audit_log
from src.core.policy import policy_engine

CHANNELS = ("sms", "email", "whatsapp", "push")
TRANSACTIONAL_PURPOSES = {"otp", "transaction_receipt", "case_update"}


class ConsentRecord(BaseModel):
    purpose: str
    granted: bool
    updated_at: float = Field(default_factory=time.time)
    source: str = "customer"          # customer | default | operator


class ContactPreferences(BaseModel):
    subscriber_id: str
    channels: Dict[str, bool] = Field(default_factory=lambda: {"sms": True, "email": True, "whatsapp": False, "push": True})
    language: Optional[str] = None
    consents: Dict[str, ConsentRecord] = Field(default_factory=dict)


class ConsentService:
    def __init__(self):
        self._prefs: Dict[str, ContactPreferences] = {}
        self._lock = threading.RLock()

    def _get(self, subscriber_id: str) -> ContactPreferences:
        with self._lock:
            prefs = self._prefs.get(subscriber_id)
            if prefs is None:
                prefs = ContactPreferences(subscriber_id=subscriber_id)
                defaults = policy_engine.section("consent", {}).get("defaults", {})
                for purpose, granted in defaults.items():
                    prefs.consents[purpose] = ConsentRecord(purpose=purpose, granted=bool(granted), source="default")
                self._prefs[subscriber_id] = prefs
            return prefs

    def get_preferences(self, subscriber_id: str) -> ContactPreferences:
        return self._get(subscriber_id).model_copy(deep=True)

    def has_consent(self, subscriber_id: str, purpose: str) -> bool:
        rec = self._get(subscriber_id).consents.get(purpose)
        return bool(rec and rec.granted)

    def record_consent(self, subscriber_id: str, purpose: str, granted: bool, *, correlation_id: str, source: str = "customer") -> ConsentRecord:
        if purpose not in policy_engine.section("consent", {}).get("purposes", []):
            raise ValueError(f"Unknown consent purpose '{purpose}'")
        with self._lock:
            prefs = self._get(subscriber_id)
            rec = ConsentRecord(purpose=purpose, granted=granted, source=source)
            prefs.consents[purpose] = rec
        audit_log.append("CONSENT", correlation_id, f"consent:{purpose}", "GRANTED" if granted else "WITHDRAWN",
                         subscriber_id=subscriber_id, actor=source)
        return rec

    def set_channel(self, subscriber_id: str, channel: str, enabled: bool, *, correlation_id: str) -> None:
        if channel not in CHANNELS:
            raise ValueError(f"Unknown channel '{channel}'")
        with self._lock:
            self._get(subscriber_id).channels[channel] = enabled
        audit_log.append("CONSENT", correlation_id, f"channel:{channel}", "ENABLED" if enabled else "DISABLED", subscriber_id=subscriber_id)

    def can_contact(self, subscriber_id: str, channel: str, purpose: str) -> bool:
        """Transactional messages need an enabled channel; marketing/proactive additionally need purpose consent."""
        prefs = self._get(subscriber_id)
        if not prefs.channels.get(channel, False) and purpose != "otp":
            return False
        if purpose in TRANSACTIONAL_PURPOSES:
            return True
        return self.has_consent(subscriber_id, purpose)

    def allowed_channels(self, subscriber_id: str, purpose: str) -> List[str]:
        return [c for c in CHANNELS if self.can_contact(subscriber_id, c, purpose)]

    def reset(self) -> None:
        with self._lock:
            self._prefs.clear()


consent_service = ConsentService()
