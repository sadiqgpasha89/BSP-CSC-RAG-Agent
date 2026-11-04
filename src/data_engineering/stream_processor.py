"""Streaming Event Bus & Telemetry Processor (Apache Kafka / GCP PubSub Simulator)."""

import threading
import time
import uuid
from collections import deque
from typing import Any, Callable, Deque, Dict, List

from pydantic import BaseModel, Field

from src.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_BUFFER_CAPACITY = 20_000


class StreamEvent(BaseModel):
    event_id: str
    topic: str
    trace_id: str
    session_id: str
    event_type: str  # e.g., "INGRESS_NORMALIZED", "AGENT_REASONING_STEP", "TOOL_EXECUTED", "GUARDRAIL_VERDICT"
    payload: Dict[str, Any]
    timestamp: float = Field(default_factory=time.time)


class StreamProcessor:
    """Message bus decoupling conversational execution from analytical lakehouse persistence.

    The in-memory buffer is a bounded ring (a real broker applies retention too); the previous unbounded list
    grew forever under load. Publishing is thread-safe because FastAPI executes sync handlers in a thread pool.
    """

    def __init__(self, capacity: int = DEFAULT_BUFFER_CAPACITY):
        self.message_buffer: Deque[StreamEvent] = deque(maxlen=capacity)
        self._subscribers: Dict[str, List[Callable[[StreamEvent], None]]] = {}
        self._lock = threading.RLock()

    def publish(self, topic: str, trace_id: str, session_id: str, event_type: str, payload: Dict[str, Any]) -> StreamEvent:
        """Publish an event to the bus and fan it out to topic and wildcard subscribers."""
        event = StreamEvent(
            event_id=f"evt-{uuid.uuid4().hex[:16]}",
            topic=topic,
            trace_id=trace_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
        with self._lock:
            self.message_buffer.append(event)
            listeners = list(self._subscribers.get(topic, [])) + list(self._subscribers.get("*", []))

        for listener in listeners:
            try:
                listener(event)
            except Exception as e:  # a faulty consumer must never break the conversational path
                logger.error("Error in stream subscriber listener", topic=topic, error=str(e))

        return event

    def subscribe(self, topic: str, callback: Callable[[StreamEvent], None]) -> None:
        """Register a callback for events published to a specific topic (or '*' for wildcard)."""
        with self._lock:
            self._subscribers.setdefault(topic, []).append(callback)

    def get_events_for_session(self, session_id: str) -> List[StreamEvent]:
        """Query buffered event trajectory for a specific session."""
        with self._lock:
            return [e for e in self.message_buffer if e.session_id == session_id]

    def clear_buffer(self) -> None:
        """Reset internal memory buffer."""
        with self._lock:
            self.message_buffer.clear()


stream_processor = StreamProcessor()
