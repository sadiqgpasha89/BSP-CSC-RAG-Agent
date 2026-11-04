"""Account & Provisioning System Adapter (eSIM, PAC Codes, Anti-Fraud SIM Swap)."""

import secrets
from typing import Dict, Any, Optional
from pydantic import BaseModel
from src.core.logging import get_logger
from src.core.security import AuthContext, security_service, CPNIScope
from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.circuit_breaker import CircuitBreaker

logger = get_logger(__name__)


class ProvisioningResult(BaseModel):
    success: bool
    action: str
    target_subscriber_id: str
    activation_token: Optional[str] = None
    security_verified: bool
    message: str


class ProvisioningAdapter:
    """Handles subscriber SIM/eSIM provisioning with strict Zero-Trust anti-fraud guardrails."""

    def __init__(self):
        self.circuit = CircuitBreaker("provisioning_adapter")

    @staticmethod
    def _urgency_block(subscriber_id: str, action: str) -> ProvisioningResult:
        logger.warning("Credential change blocked due to high-urgency social engineering heuristic", subscriber_id=subscriber_id, action=action)
        return ProvisioningResult(
            success=False,
            action=action,
            target_subscriber_id=subscriber_id,
            security_verified=False,
            message="Anti-Fraud Safeguard: SIM swap requests with emotional urgency or location anomaly must complete cryptographic Step-Up MFA or visit a retail branch with government photo ID.",
        )

    def generate_esim_profile(
        self,
        subscriber_id: str,
        device_eid: str,
        auth_context: Optional[AuthContext] = None,
        emotional_urgency_detected: bool = False,
    ) -> ProvisioningResult:
        """Generate cryptographically signed SM-DP+ eSIM activation QR payload.

        Re-profiling an eSIM is functionally a SIM swap, so it is subject to the identical zero-trust gate: it
        fails *closed* when no auth context is present, and urgency cues can never override it.
        """
        if emotional_urgency_detected:
            return self._urgency_block(subscriber_id, "ESIM_PROVISIONING")

        authorized, reason = security_service.validate_cpni_access(auth_context, CPNIScope.HIGH_RISK_CREDENTIAL)
        if not authorized:
            logger.warning("Zero-trust eSIM provisioning rejected", reason=reason)
            return ProvisioningResult(
                success=False,
                action="ESIM_PROVISIONING",
                target_subscriber_id=subscriber_id,
                security_verified=False,
                message=f"Zero-Trust Policy Block: {reason}",
            )

        # Activation matching id is a one-time secret: it must be unpredictable (CSPRNG), not a hash of public inputs + clock.
        token = f"LPA:1$cust-esim.telco.net${secrets.token_hex(8).upper()}"
        return ProvisioningResult(
            success=True,
            action="ESIM_PROVISIONING",
            target_subscriber_id=subscriber_id,
            activation_token=token,
            security_verified=True,
            message="eSIM profile successfully provisioned on SM-DP+ core. Connect device to Wi-Fi and scan QR code in Cellular settings.",
        )

    def validate_sim_swap_request(
        self,
        subscriber_id: str,
        requested_iccid: str,
        auth_context: Optional[AuthContext] = None,
        emotional_urgency_detected: bool = False,
    ) -> ProvisioningResult:
        """Evaluate SIM swap request against zero-trust anti-social-engineering boundaries."""
        # Section 10.2: Emotional urgency or unverified MFA cannot override zero-trust
        if emotional_urgency_detected:
            return self._urgency_block(subscriber_id, "SIM_SWAP_VALIDATION")

        if not auth_context or not auth_context.is_mfa_verified:
            return ProvisioningResult(
                success=False,
                action="SIM_SWAP_VALIDATION",
                target_subscriber_id=subscriber_id,
                security_verified=False,
                message="Zero-Trust Requirement: Out-of-band Step-up MFA verification required prior to SIM swap.",
            )

        # MFA flag alone is not enough: the session must also be unexpired and hold the credential-change scope.
        authorized, reason = security_service.validate_cpni_access(auth_context, CPNIScope.HIGH_RISK_CREDENTIAL)
        if not authorized:
            return ProvisioningResult(
                success=False,
                action="SIM_SWAP_VALIDATION",
                target_subscriber_id=subscriber_id,
                security_verified=False,
                message=f"Zero-Trust Requirement: {reason}",
            )

        return ProvisioningResult(
            success=True,
            action="SIM_SWAP_VALIDATION",
            target_subscriber_id=subscriber_id,
            security_verified=True,
            message=f"SIM Swap approved. New ICCID {requested_iccid} provisioned to subscriber line.",
        )

    def activate_roaming_pass(self, subscriber_id: str, plan_code: str = "ROAM_PASSPORT_2026") -> ProvisioningResult:
        """Activate international roaming passport on subscriber profile."""
        return ProvisioningResult(
            success=True,
            action="ACTIVATE_ROAMING_PASS",
            target_subscriber_id=subscriber_id,
            security_verified=True,
            message=f"Global Wanderlust International Roaming Passport ({plan_code}) successfully attached. Active for 180+ partner networks.",
        )


provisioning_adapter = ProvisioningAdapter()
