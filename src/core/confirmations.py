"""Pending consequential actions (explicit-confirmation protocol) and idempotency.

A consequential tool call is *never* executed on the first request. The gateway freezes the validated arguments into a
``PendingAction`` bound to (subscriber, session, tool, argument hash) and asks the customer to confirm it. Only the
customer-facing confirm path (a button / a "yes" in the same session) can release it. A model cannot confirm its own
proposal, the action is single-use, it expires, and its idempotency key is its own id, so a double-click or a
retried request replays the first result instead of executing twice.
"""

import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from src.core.config import settings

MAX_PENDING = 20_000
MAX_IDEMPOTENT = 20_000


class ActionStatus(str, Enum):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"     # customer confirmed; a human operator must approve execution
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"         # executed but the result is not yet known: reconcile, never retry blindly
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


def canonical_hash(tool: str, args: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class PendingAction(BaseModel):
    action_id: str
    tool: str
    args: Dict[str, Any]
    args_hash: str
    subscriber_id: str
    session_id: str
    agent: str
    summary: str
    risk: str = "unknown"
    status: ActionStatus = ActionStatus.PENDING_CONFIRMATION
    requires: List[str] = Field(default_factory=lambda: ["confirmation"])
    created_at: float = Field(default_factory=time.time)
    expires_at: float = 0.0
    confirmed_at: Optional[float] = None
    step_up_satisfied: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    correlation_id: str = ""


class PendingActionStore:
    def __init__(self):
        self._items: "OrderedDict[str, PendingAction]" = OrderedDict()
        self._lock = threading.RLock()

    def create(self, *, tool: str, args: Dict[str, Any], subscriber_id: str, session_id: str, agent: str,
               summary: str, risk: str, requires: List[str], correlation_id: str) -> PendingAction:
        with self._lock:
            # Re-proposing the same open action in the same session returns the existing one (no confirmation spam).
            h = canonical_hash(tool, args)
            for existing in self._items.values():
                if (existing.session_id == session_id and existing.subscriber_id == subscriber_id and existing.args_hash == h
                        and existing.status == ActionStatus.PENDING_CONFIRMATION and existing.expires_at > time.time()):
                    return existing
            act = PendingAction(
                action_id=f"act-{uuid.uuid4().hex[:12]}", tool=tool, args=args, args_hash=h, subscriber_id=subscriber_id,
                session_id=session_id, agent=agent, summary=summary, risk=risk, requires=requires,
                expires_at=time.time() + settings.confirmation_ttl_sec, correlation_id=correlation_id,
            )
            self._items[act.action_id] = act
            while len(self._items) > MAX_PENDING:
                self._items.popitem(last=False)
            return act

    def get(self, action_id: str) -> Optional[PendingAction]:
        with self._lock:
            act = self._items.get(action_id)
            if act and act.status == ActionStatus.PENDING_CONFIRMATION and act.expires_at <= time.time():
                act.status = ActionStatus.EXPIRED
            return act

    def open_for_session(self, session_id: str, subscriber_id: str) -> List[PendingAction]:
        with self._lock:
            out = []
            for act in self._items.values():
                if act.session_id == session_id and act.subscriber_id == subscriber_id:
                    if self.get(act.action_id).status == ActionStatus.PENDING_CONFIRMATION:
                        out.append(act)
            return out

    def awaiting_approval(self) -> List[PendingAction]:
        with self._lock:
            return [a for a in self._items.values() if a.status == ActionStatus.AWAITING_APPROVAL]

    def all(self) -> List[PendingAction]:
        with self._lock:
            return list(self._items.values())

    def reset(self) -> None:
        with self._lock:
            self._items.clear()


class IdempotencyStore:
    """First-writer-wins result cache with in-flight de-duplication (concurrent identical requests execute once)."""

    def __init__(self, capacity: int = MAX_IDEMPOTENT):
        self._results: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._inflight: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._capacity = capacity

    def begin(self, key: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """Return ("replay", result) | ("owner", None) | ("wait", None)."""
        with self._lock:
            if key in self._results:
                return "replay", self._results[key]
            if key in self._inflight:
                return "wait", None
            self._inflight[key] = threading.Event()
            return "owner", None

    def wait(self, key: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
        with self._lock:
            ev = self._inflight.get(key)
        if ev is not None:
            ev.wait(timeout)
        with self._lock:
            return self._results.get(key)

    def finish(self, key: str, result: Optional[Dict[str, Any]]) -> None:
        """Record a *definitive* result; pass None to release the key without caching (e.g. transient failure)."""
        with self._lock:
            if result is not None:
                self._results[key] = result
                while len(self._results) > self._capacity:
                    self._results.popitem(last=False)
            ev = self._inflight.pop(key, None)
        if ev is not None:
            ev.set()

    def reset(self) -> None:
        with self._lock:
            self._results.clear()
            self._inflight.clear()


pending_actions = PendingActionStore()
idempotency_store = IdempotencyStore()
