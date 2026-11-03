"""Executive Metrics & Cost Arbitrage Telemetry API Router."""

from fastapi import APIRouter
from src.metrics.engine import metrics_engine, UnifiedMetricsSnapshot
from src.core.hybrid_router import hybrid_router

router = APIRouter(prefix="/metrics", tags=["Operational & Executive Metrics"])


@router.get("/summary", response_model=UnifiedMetricsSnapshot)
def get_metrics_summary():
    """Return synchronized real-time snapshot of all 6 operational metric categories."""
    return metrics_engine.get_snapshot()


@router.get("/cost-arbitrage")
def get_hybrid_arbitrage_telemetry():
    """Return OpenStack on-premises vs GCP public cloud token economics and cost savings."""
    return hybrid_router.get_arbitrage_telemetry()
