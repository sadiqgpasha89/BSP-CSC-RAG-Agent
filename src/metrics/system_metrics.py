"""System & Infrastructure Metrics (Section 8.4 & 8.6)."""

from typing import List

import numpy as np
from pydantic import BaseModel, Field

from src.data_engineering.lakehouse_sink import lakehouse_sink

# Below this many completed sessions a percentile is statistical noise; the reference baseline is served instead.
MIN_LIVE_SAMPLES = 50


class SystemMetrics(BaseModel):
    p95_latency_ms: float
    p99_latency_ms: float
    time_to_first_token_ms: float
    inter_token_latency_ms: float
    token_throughput_per_sec: float
    gpu_vram_utilization_pct: float
    gpu_sm_compute_efficiency_pct: float
    api_gateway_error_rate_pct: float
    data_source: str = "reference_baseline"  # "live" once MIN_LIVE_SAMPLES sessions have been observed
    reference_fields: List[str] = Field(default_factory=list)  # fields NOT derived from measured telemetry


class SystemMetricsCalculator:
    """Calculates computing infrastructure and latency percentiles."""

    # Reference values for signals the simulator cannot measure (no GPU / token streaming in this deployment).
    REFERENCE = dict(
        p95_latency_ms=310.5,
        p99_latency_ms=485.2,
        time_to_first_token_ms=185.0,  # Below 350ms voice target
        inter_token_latency_ms=18.4,   # Below 30ms/token target
        token_throughput_per_sec=245.8,
        gpu_vram_utilization_pct=76.4,
        gpu_sm_compute_efficiency_pct=84.2,
        api_gateway_error_rate_pct=0.04,
    )

    def compute(self) -> SystemMetrics:
        """Derive P95/P99 latency from measured session durations when enough traffic exists."""
        values = dict(self.REFERENCE)
        reference = list(values)
        source = "reference_baseline"

        df = lakehouse_sink.query_sessions_dataframe()
        if len(df) >= MIN_LIVE_SAMPLES and "duration_sec" in df:
            latencies_ms = df["duration_sec"].astype(float).to_numpy() * 1000.0
            values["p95_latency_ms"] = round(float(np.percentile(latencies_ms, 95)), 2)
            values["p99_latency_ms"] = round(float(np.percentile(latencies_ms, 99)), 2)
            reference = [f for f in reference if f not in ("p95_latency_ms", "p99_latency_ms")]
            source = "live"

        return SystemMetrics(**values, data_source=source, reference_fields=reference)


system_metrics_calculator = SystemMetricsCalculator()
