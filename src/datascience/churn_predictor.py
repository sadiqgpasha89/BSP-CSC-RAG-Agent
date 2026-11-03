"""Customer Churn Risk Scrutiny & Feature Attribution Engine (Stage 2)."""

from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


class ChurnPredictionResult(BaseModel):
    churn_probability: float
    is_high_churn_risk: bool
    risk_level: str  # "LOW", "MODERATE", "HIGH", "CRITICAL"
    top_risk_factors: List[str] = Field(default_factory=list)
    recommended_retention_action: Optional[str] = None


class ChurnPredictor:
    """Predicts customer churn likelihood using behavioral and operational telemetry."""

    def predict(
        self,
        tenure_months: int,
        recent_disputes_count: int,
        unbilled_usage_usd: float,
        billing_status: str,
        current_sentiment: float,
        is_in_outage_sector: bool = False,
    ) -> ChurnPredictionResult:
        """Compute churn probability delta_churn and feature attribution."""
        base_risk = 0.12
        risk_factors: List[str] = []

        # 1. Billing disputes attribution
        if recent_disputes_count >= 2:
            base_risk += 0.35
            risk_factors.append(f"Multiple unresolved billing disputes ({recent_disputes_count})")
        elif recent_disputes_count == 1:
            base_risk += 0.15
            risk_factors.append("Recent billing dispute on record")

        # 2. Unbilled or disputed billing status
        if billing_status in ["disputed_unpaid", "overdue"]:
            base_risk += 0.20
            risk_factors.append(f"Account billing status is '{billing_status}'")

        # 3. Sentiment impact
        if current_sentiment <= -0.6:
            base_risk += 0.25
            risk_factors.append("Severe negative customer sentiment during active session")
        elif current_sentiment < -0.2:
            base_risk += 0.10
            risk_factors.append("Elevated customer frustration")

        # 4. Outage exposure
        if is_in_outage_sector:
            base_risk += 0.15
            risk_factors.append("Active localized network infrastructure disruption in subscriber sector")

        # 5. Tenure mitigation
        if tenure_months > 36:
            base_risk -= 0.10  # Loyal customer base stickiness
        elif tenure_months < 6:
            base_risk += 0.10
            risk_factors.append("Early tenure onboarding risk (< 6 months)")

        churn_prob = max(0.02, min(0.99, round(base_risk, 4)))
        is_high = churn_prob > settings.churn_risk_threshold

        if churn_prob >= 0.80:
            level = "CRITICAL"
            action = "Trigger autonomous Retention Worker Agent with maximum discretionary concession tier"
        elif churn_prob >= 0.60:
            level = "HIGH"
            action = "Offer personalized loyalty data credit or plan optimization"
        elif churn_prob >= 0.35:
            level = "MODERATE"
            action = "Standard proactive issue resolution"
        else:
            level = "LOW"
            action = "Standard self-service care"

        return ChurnPredictionResult(
            churn_probability=churn_prob,
            is_high_churn_risk=is_high,
            risk_level=level,
            top_risk_factors=risk_factors,
            recommended_retention_action=action,
        )


churn_predictor = ChurnPredictor()
