"""Conversation and resumable-workflow state (short-term memory).

Only *sanitised* text is stored (PII is masked at ingress), turns are capped, and conversations expire, so memory
holds only what is necessary and permitted. The in-memory store defines the interface; production replaces it with
Redis (TTL keys) without touching callers (docs/TSD.md §6).
"""

import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.core.config import settings

MAX_TURNS = 60
MAX_CONVERSATIONS = 20_000


class Turn(BaseModel):
    role: str                      # customer | assistant | human_agent | system
    text: str                      # sanitised
    ts: float = Field(default_factory=time.time)
    intent: Optional[str] = None
    language: Optional[str] = None


class WorkflowRun(BaseModel):
    """A resumable multi-turn task: a named state machine plus its slot data."""
    name: str
    step: str
    data: Dict[str, Any] = Field(default_factory=dict)
    owner_agent: str = ""
    started_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    attempts: int = 0              # invalid-input attempts at the current step

    def touch(self, step: Optional[str] = None) -> None:
        if step:
            if step != self.step:
                self.attempts = 0
            self.step = step
        self.updated_at = time.time()


class Conversation(BaseModel):
    session_id: str
    subscriber_id: str
    channel: str = "web"
    language: str = "en"
    created_at: float = Field(default_factory=time.time)
    last_active: float = Field(default_factory=time.time)
    turns: List[Turn] = Field(default_factory=list)
    workflow: Optional[WorkflowRun] = None
    pending_action_ids: List[str] = Field(default_factory=list)
    stepup_verified_until: float = 0.0
    handed_over: bool = False
    handover_id: Optional[str] = None
    handover_state: Optional[str] = None            # PENDING | ACTIVE | RESOLVED
    handover_agent_label: Optional[str] = None
    sentiments: List[float] = Field(default_factory=list)
    intents_seen: List[str] = Field(default_factory=list)
    transaction_refs: List[str] = Field(default_factory=list)
    resolved: Optional[bool] = None
    csat: Optional[int] = None
    notes: Dict[str, Any] = Field(default_factory=dict)

    def add_turn(self, role: str, text: str, intent: Optional[str] = None, language: Optional[str] = None) -> None:
        self.turns.append(Turn(role=role, text=text[:1000], intent=intent, language=language))
        if len(self.turns) > MAX_TURNS:
            self.turns = self.turns[-MAX_TURNS:]
        self.last_active = time.time()

    @property
    def step_up_active(self) -> bool:
        return time.time() < self.stepup_verified_until


class ConversationStore:
    """Thread-safe LRU + TTL store keyed by session id, bound to a subscriber (cross-customer access is refused)."""

    def __init__(self, ttl_sec: Optional[int] = None, capacity: int = MAX_CONVERSATIONS):
        self._items: "OrderedDict[str, Conversation]" = OrderedDict()
        self._lock = threading.RLock()
        self._ttl = ttl_sec
        self._capacity = capacity

    @property
    def ttl(self) -> int:
        return self._ttl if self._ttl is not None else settings.conversation_ttl_sec

    def get_or_create(self, session_id: str, subscriber_id: str, channel: str = "web", language: str = "en") -> Conversation:
        with self._lock:
            self._evict_expired()
            conv = self._items.get(session_id)
            if conv is not None:
                if conv.subscriber_id != subscriber_id:
                    # A session id is not a credential: never hand one customer's conversation to another.
                    raise PermissionError("Session belongs to a different subscriber")
                self._items.move_to_end(session_id)
                return conv
            conv = Conversation(session_id=session_id, subscriber_id=subscriber_id, channel=channel, language=language)
            self._items[session_id] = conv
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)
            return conv

    def get(self, session_id: str) -> Optional[Conversation]:
        with self._lock:
            self._evict_expired()
            return self._items.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._items.pop(session_id, None) is not None

    def purge_subscriber(self, subscriber_id: str) -> int:
        """Data-governance hook: delete every conversation of a subscriber (right-to-erasure / retention)."""
        with self._lock:
            ids = [sid for sid, c in self._items.items() if c.subscriber_id == subscriber_id]
            for sid in ids:
                del self._items[sid]
            return len(ids)

    def _evict_expired(self) -> None:
        cutoff = time.time() - self.ttl
        stale = [sid for sid, c in self._items.items() if c.last_active < cutoff]
        for sid in stale:
            del self._items[sid]

    def all(self) -> list:
        with self._lock:
            return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def reset(self) -> None:
        with self._lock:
            self._items.clear()


conversation_store = ConversationStore()
