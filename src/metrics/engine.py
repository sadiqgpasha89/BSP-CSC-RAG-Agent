"""Central Metrics Engine aggregating 6 KPI Categories."""

from typing import Dict, Any
from pydantic import BaseModel
from src.metrics.business_metrics import business_metrics_calculator, BusinessMetrics
from src.metrics.system_metrics import system_metrics_calculator, SystemMetrics
from src.metrics.rag_metrics import rag_metrics_calculator, RAGMetrics
from src.metrics.agentic_metrics import agentic_metrics_calculator, AgenticMetrics
from src.metrics.ml_metrics import ml_metrics_calculator, MLMetrics
from src.llmops.cost_optimizer import cost_optimizer, CostSummary


class UnifiedMetricsSnapshot(BaseModel):
    business: BusinessMetrics
    system: SystemMetrics
    rag: RAGMetrics
    agentic: AgenticMetrics
    ml_nlp: MLMetrics
    cost_arbitrage: CostSummary


class MetricsEngine:
    """Consolidates real-time telemetry across all 6 architectural metric dimensions."""

    def get_snapshot(self) -> UnifiedMetricsSnapshot:
        """Produce synchronized snapshot of all enterprise operational metrics."""
        return UnifiedMetricsSnapshot(
            business=business_metrics_calculator.compute(),
            system=system_metrics_calculator.compute(),
            rag=rag_metrics_calculator.compute(),
            agentic=agentic_metrics_calculator.compute(),
            ml_nlp=ml_metrics_calculator.compute(),
            cost_arbitrage=cost_optimizer.calculate_summary(),
        )


metrics_engine = MetricsEngine()
