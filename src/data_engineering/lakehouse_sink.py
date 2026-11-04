"""Analytical Data Lakehouse Sink (Google BigQuery Table Simulator)."""

import threading
from collections import deque
from typing import Any, Deque, Dict, Optional
import pandas as pd
from src.core.config import settings
from src.core.logging import get_logger
from src.data_engineering.stream_processor import StreamEvent, stream_processor

logger = get_logger(__name__)


class BigQueryLakehouseSink:
    """Manages immutable, non-repudiation event logs and trajectory tables in BigQuery format."""

    MAX_RECORDS = 50_000  # bounded retention for the in-memory simulator

    def __init__(self, dataset_name: Optional[str] = None):
        self.dataset_name = dataset_name or settings.gcp_bigquery_dataset
        self.session_records: Deque[Dict[str, Any]] = deque(maxlen=self.MAX_RECORDS)
        self.trajectory_records: Deque[Dict[str, Any]] = deque(maxlen=self.MAX_RECORDS)
        self.guardrail_records: Deque[Dict[str, Any]] = deque(maxlen=self.MAX_RECORDS)
        self._lock = threading.Lock()

        # Auto-subscribe to stream processor events
        stream_processor.subscribe("*", self._handle_stream_event)

    def _handle_stream_event(self, event: StreamEvent) -> None:
        """Route streaming events into corresponding BigQuery analytical tables."""
        with self._lock:
            self._route_event(event)

    def _route_event(self, event: StreamEvent) -> None:
        if event.event_type == "AGENT_REASONING_STEP":
            self.trajectory_records.append({
                "trace_id": event.trace_id,
                "session_id": event.session_id,
                "timestamp": event.timestamp,
                "agent_name": event.payload.get("agent_name"),
                "step_index": event.payload.get("step_index"),
                "action": event.payload.get("action"),
                "tool_name": event.payload.get("tool_name"),
                "tool_status": event.payload.get("tool_status"),
                "latency_ms": event.payload.get("latency_ms", 0.0),
            })
        elif event.event_type == "GUARDRAIL_VERDICT":
            self.guardrail_records.append({
                "trace_id": event.trace_id,
                "session_id": event.session_id,
                "timestamp": event.timestamp,
                "passed": event.payload.get("passed", True),
                "faithfulness_score": event.payload.get("faithfulness_score", 1.0),
                "violations": event.payload.get("violations", []),
            })
        elif event.event_type == "SESSION_COMPLETED":
            self.session_records.append({
                "trace_id": event.trace_id,
                "session_id": event.session_id,
                "timestamp": event.timestamp,
                "subscriber_id": event.payload.get("subscriber_id"),
                "duration_sec": event.payload.get("duration_sec", 0.0),
                "total_tokens": event.payload.get("total_tokens", 0),
                "cost_usd": event.payload.get("cost_usd", 0.0),
                "fcr_resolved": event.payload.get("fcr_resolved", True),
                "escalated_to_human": event.payload.get("escalated_to_human", False),
                "customer_sentiment": event.payload.get("customer_sentiment", 0.0),
            })

    def query_sessions_dataframe(self) -> pd.DataFrame:
        """Return analytical DataFrame of completed sessions."""
        if not self.session_records:
            return pd.DataFrame(columns=["trace_id", "session_id", "duration_sec", "cost_usd", "fcr_resolved", "escalated_to_human"])
        with self._lock:
            return pd.DataFrame(list(self.session_records))

    def query_trajectories_dataframe(self) -> pd.DataFrame:
        """Return analytical DataFrame of agent tool execution trajectories."""
        if not self.trajectory_records:
            return pd.DataFrame(columns=["trace_id", "session_id", "agent_name", "tool_name", "tool_status", "latency_ms"])
        with self._lock:
            return pd.DataFrame(list(self.trajectory_records))

    def query_guardrails_dataframe(self) -> pd.DataFrame:
        """Return analytical DataFrame of guardrail verdicts."""
        with self._lock:
            return pd.DataFrame(list(self.guardrail_records))

    def get_summary_statistics(self) -> Dict[str, Any]:
        """Aggregate high-level Lakehouse metrics."""
        df_sess = self.query_sessions_dataframe()
        total_sessions = len(df_sess)
        if total_sessions == 0:
            return {
                "total_logged_sessions": 0,
                "fcr_rate_pct": 100.0,
                "escalation_rate_pct": 0.0,
                "avg_duration_sec": 0.0,
                "total_cost_usd": 0.0,
            }

        fcr_count = int(df_sess["fcr_resolved"].sum()) if "fcr_resolved" in df_sess else 0
        esc_count = int(df_sess["escalated_to_human"].sum()) if "escalated_to_human" in df_sess else 0

        return {
            "total_logged_sessions": total_sessions,
            "fcr_rate_pct": round((fcr_count / total_sessions) * 100.0, 2),
            "escalation_rate_pct": round((esc_count / total_sessions) * 100.0, 2),
            "avg_duration_sec": round(df_sess["duration_sec"].mean(), 2),
            "total_cost_usd": round(df_sess["cost_usd"].sum(), 4),
        }


lakehouse_sink = BigQueryLakehouseSink()
