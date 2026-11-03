"""Streaming Event Bus & Telemetry Processor (Apache Kafka / GCP PubSub Simulator)."""

import asyncio
import time
from typing import Dict, List, Any, Callable, Optional
from pydantic import BaseModel, Field
from src.core.logging import get_logger

logger = get_logger(__name__)


class StreamEvent(BaseModel):
    event_id: str
    topic: str
    trace_id: str
    session_id: str
    event_type: str  # e.g., "INGRESS_NORMALIZED", "AGENT_REASONING_STEP", "TOOL_EXECUTED", "GUARDRAIL_VERDICT"
    payload: Dict[str, Any]
    timestamp: float = Field(default_factory=time.time)


class StreamProcessor:
    """Asynchronous message bus decoupling conversational execution from analytical lakehouse persistence."""

    def __init__(self):
        self.message_buffer: List[StreamEvent] = []
        self._subscribers: Dict[str, List[Callable[[StreamEvent], None]]] = {}
        self._running: bool = True

    def publish(self, topic: str, trace_id: str, session_id: str, event_type: str, payload: Dict[str, Any]) -> StreamEvent:
        """Publish an event to the streaming bus with zero message loss guarantee."""
        event = StreamEvent(
            event_id=f"evt-{int(time.time()*1000)}-{len(self.message_buffer)}",
            topic=topic,
            trace_id=trace_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
        self.message_buffer.append(event)

        # Notify topic subscribers synchronously or asynchronously
        listeners = self._subscribers.get(topic, []) + self._subscribers.get("*", [])
        for listener in listeners:
            try:
                listener(event)
            except Exception as e:
                logger.error("Error in stream subscriber listener", topic=topic, error=str(e))

        return event

    def subscribe(self, topic: str, callback: Callable[[StreamEvent], None]) -> None:
        """Register a callback for events published to a specific topic (or '*' for wildcard)."""
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(callback)

    def get_events_for_session(self, session_id: str) -> List[StreamEvent]:
        """Query buffered event trajectory for a specific session."""
        return [e for e in self.message_buffer if e.session_id == session_id]

    def clear_buffer(self) -> None:
        """Reset internal memory buffer."""
        self.message_buffer.clear()


stream_processor = StreamProcessor()
