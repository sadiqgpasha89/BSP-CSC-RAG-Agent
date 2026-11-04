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

        # Any other (modern or unknown) plan must never be shown grandfathered terms: exclude legacy chunks.
        # Defaulting unknown plans to "unrestricted", as before, let legacy tariffs leak to unclassified accounts.
        return {"is_legacy": False}


temporal_filter_engine = TemporalFilterEngine()
