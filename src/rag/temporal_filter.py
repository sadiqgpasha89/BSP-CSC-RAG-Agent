"""Temporal & Contractual Metadata Filtering Engine (Mitigating Legacy Plan Traps)."""

from typing import Dict, Any, Optional
from src.core.logging import get_logger

logger = get_logger(__name__)


class TemporalFilterEngine:
    """Pre-filters RAG candidate corpus based on customer contract date, plan code, and legacy status."""

    @staticmethod
    def build_filters(
        subscriber_plan_code: Optional[str] = None,
        query: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Construct deterministic pre-retrieval filters to ensure historical or modern contract fidelity."""
        query_lower = query.lower()

        # If user explicitly asks to compare with new plans or upgrade, permit unrestricted search
        if any(w in query_lower for w in ["upgrade", "switch plan", "new plan", "2026 plan", "compare"]):
            return None

        # If subscriber is on legacy grandfathered plan, isolate retrieval to their grandfathered terms
        if subscriber_plan_code == "FREEDOM_UNLIMITED_2018":
            logger.info("Applying strict temporal legacy filter for subscriber on FREEDOM_UNLIMITED_2018")
            return {"plan_code": "FREEDOM_UNLIMITED_2018"}

        # If subscriber is on 2026 5G plan, exclude legacy grandfathered chunks
        if subscriber_plan_code in ["5G_ULTRA_MAX_2026", "FIBER_GIGA_1G_2026"]:
            return {"is_legacy": False}

        return None


temporal_filter_engine = TemporalFilterEngine()
