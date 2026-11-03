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
        total_requests = telemetry["total_routed_requests"]
        # Estimate average 600 tokens per interaction
        total_tokens = max(600, total_requests * 600)

        # Baseline cost if all ran on public cloud frontier APIs ($0.0025 per 1k average)
        benchmark_cloud_cost = (total_tokens / 1000.0) * 0.0025
        net_savings = telemetry["cumulative_arbitrage_savings_usd"]
        actual_cost = max(0.001, benchmark_cloud_cost - net_savings)

        savings_pct = (net_savings / benchmark_cloud_cost * 100.0) if benchmark_cloud_cost > 0 else 0.0
        avg_cost_1k = (actual_cost / total_tokens) * 1000.0

        return CostSummary(
            total_tokens_processed=total_tokens,
            openstack_baseline_cost_usd=round(actual_cost, 4),
            gcp_cloud_cost_usd=round(benchmark_cloud_cost, 4),
            net_savings_usd=round(net_savings, 4),
            savings_percentage=round(savings_pct, 2),
            avg_cost_per_1k_tokens=round(avg_cost_1k, 5),
        )


cost_optimizer = CostOptimizer()
