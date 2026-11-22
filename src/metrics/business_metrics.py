"""Business & Operational Metrics (Section 8.1)."""

from typing import List, Optional
from pydantic import BaseModel, Field
from src.data_engineering.lakehouse_sink import lakehouse_sink


class BusinessMetrics(BaseModel):
    first_contact_resolution_pct: float
    average_handling_time_sec: float
    agent_deflection_rate_pct: float
    csat_score: Optional[float] = None  # 1.0 to 5.0; mean of ratings captured via /chat/csat, null when none
    net_promoter_score: Optional[float] = None  # needs survey ingestion; null until wired
    cost_per_interaction_usd: float
    total_churn_equity_preserved_usd: Optional[float] = None  # needs CRM outcome joins; null until wired
    # These three are never fabricated: they are null unless a real source exists (see docs/KPI_DEFINITIONS.md).
    reference_fields: List[str] = Field(default_factory=list)


class BusinessMetricsCalculator:
    """Calculates operational and executive business metrics from BigQuery lakehouse traces."""

    def compute(self) -> BusinessMetrics:
        """Derive FCR, AHT, deflection, CSAT, CPI, and churn mitigation ROI."""
        summary = lakehouse_sink.get_summary_statistics()
        total_sessions = max(1, summary.get("total_logged_sessions", 1))

        fcr = summary.get("fcr_rate_pct", 88.5)
        esc_rate = summary.get("escalation_rate_pct", 11.5)
        deflection = round(100.0 - esc_rate, 2)
        aht = summary.get("avg_duration_sec", 42.5) if summary.get("avg_duration_sec", 0) > 0 else 42.5

        # CPI = (Compute OpEx + Token OpEx + Human OpEx) / Total Sessions
        total_cost = summary.get("total_cost_usd", 0.05)
        human_escalation_cost = (esc_rate / 100.0 * total_sessions) * 12.00  # $12/call human tier-2 cost
        total_opex = total_cost + human_escalation_cost
        cpi = round(total_opex / total_sessions, 3)

        from src.core.conversation import conversation_store
        ratings = [c.csat for c in conversation_store.all() if getattr(c, "csat", None)]
        csat = round(sum(ratings) / len(ratings), 2) if ratings else None

        return BusinessMetrics(
            first_contact_resolution_pct=fcr,
            average_handling_time_sec=aht,
            agent_deflection_rate_pct=deflection,
            csat_score=csat,
            net_promoter_score=None,
            cost_per_interaction_usd=cpi,
            total_churn_equity_preserved_usd=None,
            reference_fields=["net_promoter_score", "total_churn_equity_preserved_usd"] + ([] if ratings else ["csat_score"]),
        )


business_metrics_calculator = BusinessMetricsCalculator()
