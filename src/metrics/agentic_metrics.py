"""Agentic Reasoning Metrics (Section 8.2)."""

from typing import List

from pydantic import BaseModel, Field

from src.data_engineering.lakehouse_sink import lakehouse_sink
from src.metrics.system_metrics import MIN_LIVE_SAMPLES


class AgenticMetrics(BaseModel):
    agent_path_efficiency: float  # eta = K* / K
    tool_invocation_success_rate_pct: float  # TISR
    plan_deviation_index: float
    autonomous_replanning_rate_pct: float
    goal_completion_rate_pct: float  # GCR
    data_source: str = "reference_baseline"
    reference_fields: List[str] = Field(default_factory=list)


class AgenticMetricsCalculator:
    """Evaluates agentic reasoning optimality and execution trajectory fidelity."""

    REFERENCE = dict(
        agent_path_efficiency=0.912,
        tool_invocation_success_rate_pct=98.6,
        plan_deviation_index=0.084,
        autonomous_replanning_rate_pct=4.2,
        goal_completion_rate_pct=96.8,
    )

    def compute(self) -> AgenticMetrics:
        """TISR and goal completion are computed from recorded trajectories / sessions once enough data exists."""
        values = dict(self.REFERENCE)
        reference = list(values)
        source = "reference_baseline"

        traj = lakehouse_sink.query_trajectories_dataframe()
        if "tool_status" in traj:
            tool_calls = traj[traj["tool_status"].isin(["SUCCESS", "FAILURE"])]
            if len(tool_calls) >= MIN_LIVE_SAMPLES:
                values["tool_invocation_success_rate_pct"] = round(float((tool_calls["tool_status"] == "SUCCESS").mean()) * 100.0, 2)
                reference.remove("tool_invocation_success_rate_pct")
                source = "live"

        sessions = lakehouse_sink.query_sessions_dataframe()
        if len(sessions) >= MIN_LIVE_SAMPLES and "escalated_to_human" in sessions:
            values["goal_completion_rate_pct"] = round(float((~sessions["escalated_to_human"].astype(bool)).mean()) * 100.0, 2)
            reference.remove("goal_completion_rate_pct")
            source = "live"

        return AgenticMetrics(**values, data_source=source, reference_fields=reference)


agentic_metrics_calculator = AgenticMetricsCalculator()
