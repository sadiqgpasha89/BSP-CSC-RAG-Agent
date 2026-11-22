"""Tamper-evident audit trail (hash-chained, PII-masked).

Every identity check, policy decision, tool call, confirmation, API result and transaction reference is appended
here. Each record embeds the SHA-256 of the previous record, so editing, removing or re-ordering any record breaks
``verify_chain``. In production the sink is a WORM bucket / append-only table (see docs/TSD.md §9); the in-memory
implementation below is the reference semantics and the test double.
"""

import hashlib
import json
import re
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from pydantic import BaseModel, Field

GENESIS_HASH = "0" * 64
MAX_RECORDS = 100_000

# Field names whose values are never written to the audit trail, whatever they contain.
SECRET_KEYS = {
    "otp", "code", "pin", "password", "secret", "token", "session_token", "payment_token", "card_number", "cvv",
    "activation_token", "authorization", "api_key",
}
_MSISDN_RE = re.compile(r"(?<![\w<])\+?\d[\d\s\-().]{8,14}\d(?![\w>])")
_CARD_RE = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")


def mask_text(value: str) -> str:
    """Mask card numbers and phone-number-like digit runs inside free text."""
    value = _CARD_RE.sub("<CARD>", value)
    return _MSISDN_RE.sub(lambda m: "<PHONE:***" + re.sub(r"\D", "", m.group(0))[-2:] + ">", value)


def mask_payload(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact secrets and mask PII so audit/log/trace payloads carry no unnecessary personal data."""
    if _depth > 6:
        return "<truncated>"
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).lower() in SECRET_KEYS:
                out[k] = "<REDACTED>"
            elif str(k).lower() in {"msisdn", "phone", "mobile"} and isinstance(v, str):
                out[k] = "***" + re.sub(r"\D", "", v)[-4:]
            else:
                out[k] = mask_payload(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [mask_payload(v, _depth + 1) for v in obj[:50]]
    if isinstance(obj, str):
        return mask_text(obj)[:500]
    return obj


class AuditRecord(BaseModel):
    seq: int
    record_id: str
    ts: float
    event_type: str            # e.g. IDENTITY_CHECK, POLICY_DECISION, TOOL_CALL, CONFIRMATION, TRANSACTION, HANDOVER
    correlation_id: str        # trace id: conversation -> orchestration -> agent -> tool -> enterprise API
    session_id: Optional[str] = None
    subscriber_id: Optional[str] = None
    actor: str = "system"      # agent name, "customer", or an operator id
    action: str = ""
    outcome: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)
    prev_hash: str = GENESIS_HASH
    hash: str = ""


def _digest(rec: AuditRecord) -> str:
    body = rec.model_dump(exclude={"hash"})
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only hash chain. Thread-safe."""

    def __init__(self, capacity: int = MAX_RECORDS):
        self._records: Deque[AuditRecord] = deque(maxlen=capacity)
        self._lock = threading.RLock()
        self._seq = 0
        self._head = GENESIS_HASH

    def append(
        self,
        event_type: str,
        correlation_id: str,
        action: str,
        outcome: str,
        *,
        session_id: Optional[str] = None,
        subscriber_id: Optional[str] = None,
        actor: str = "system",
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditRecord:
        with self._lock:
            self._seq += 1
            rec = AuditRecord(
                seq=self._seq,
                record_id=f"aud-{uuid.uuid4().hex[:12]}",
                ts=time.time(),
                event_type=event_type,
                correlation_id=correlation_id,
                session_id=session_id,
                subscriber_id=subscriber_id,
                actor=actor,
                action=action,
                outcome=outcome,
                details=mask_payload(details or {}),
                prev_hash=self._head,
            )
            rec.hash = _digest(rec)
            self._head = rec.hash
            self._records.append(rec)
            return rec

    def query(
        self,
        *,
        correlation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        subscriber_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[AuditRecord]:
        with self._lock:
            rows = [
                r for r in self._records
                if (correlation_id is None or r.correlation_id == correlation_id)
                and (session_id is None or r.session_id == session_id)
                and (subscriber_id is None or r.subscriber_id == subscriber_id)
                and (event_type is None or r.event_type == event_type)
            ]
        return rows[-limit:]

    def verify_chain(self) -> Dict[str, Any]:
        """Recompute every hash and link. Returns the first broken sequence number, if any."""
        with self._lock:
            records = list(self._records)
        prev: Optional[str] = None
        for rec in records:
            if prev is not None and rec.prev_hash != prev:
                return {"valid": False, "broken_at_seq": rec.seq, "reason": "prev_hash mismatch", "records": len(records)}
            if _digest(rec) != rec.hash:
                return {"valid": False, "broken_at_seq": rec.seq, "reason": "record hash mismatch", "records": len(records)}
            prev = rec.hash
        return {"valid": True, "broken_at_seq": None, "reason": None, "records": len(records), "head": self._head}

    def __len__(self) -> int:
        return len(self._records)

    def reset(self) -> None:
        """Test helper only; production sinks are append-only."""
        with self._lock:
            self._records.clear()
            self._seq = 0
            self._head = GENESIS_HASH


audit_log = AuditLog()
