"""Cost Per Token & Hybrid Arbitrage Optimizer."""

from typing import Dict, Any
from pydantic import BaseModel
from src.core.hybrid_router import hybrid_router


class CostSummary(BaseModel):
    total_tokens_processed: int
    openstack_baseline_cost_usd: float
    gcp_cloud_cost_usd: float
    net_savings_usd: float
    savings_percentage: float
    avg_cost_per_1k_tokens: float


class CostOptimizer:
    """Calculates granular token metrics and validates hybrid cloud financial savings."""

    def __init__(self):
        self.router = hybrid_router

    def calculate_summary(self) -> CostSummary:
        """Aggregate token economics and compute financial arbitrage ROI."""
        telemetry = self.router.get_arbitrage_telemetry()
        # Use the router's measured tokens and costs. The previous version invented "600 tokens per request" and a
        # flat $0.0025/1k cloud price, so the reported savings did not correspond to any routed traffic.
        total_tokens = telemetry["total_tokens_routed"]
        benchmark_cloud_cost = telemetry["cumulative_cloud_benchmark_cost_usd"]
        actual_cost = telemetry["cumulative_actual_cost_usd"]
        net_savings = telemetry["cumulative_arbitrage_savings_usd"]

        savings_pct = (net_savings / benchmark_cloud_cost * 100.0) if benchmark_cloud_cost > 0 else 0.0
        avg_cost_1k = (actual_cost / total_tokens * 1000.0) if total_tokens > 0 else 0.0

        return CostSummary(
            total_tokens_processed=total_tokens,
            openstack_baseline_cost_usd=round(actual_cost, 6),
            gcp_cloud_cost_usd=round(benchmark_cloud_cost, 6),
            net_savings_usd=round(net_savings, 6),
            savings_percentage=round(savings_pct, 2),
            avg_cost_per_1k_tokens=round(avg_cost_1k, 6),
        )


cost_optimizer = CostOptimizer()
