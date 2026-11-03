"""Enterprise Security, CPNI Authorization & Zero-Trust Access Control."""

import hmac
import hashlib
import time
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


class CPNIScope:
    """CPNI Access Scopes under Federal Communications Commission (FCC) Regulations."""
    VIEW_ACCOUNT = "cpni:view:account"
    VIEW_BILLING = "cpni:view:billing"
    VIEW_CALL_RECORDS = "cpni:view:cdr"
    MODIFY_SERVICES = "cpni:modify:services"
    HIGH_RISK_CREDENTIAL = "cpni:modify:credential_sim"
    FINANCIAL_CREDIT = "cpni:modify:billing_credit"


class AuthContext(BaseModel):
    """Authenticated Customer Context and CPNI Scopes."""
    session_id: str
    subscriber_id: str
    msisdn: str
    auth_level: str = "authenticated_mfa"  # unauthenticated, basic_pin, authenticated_mfa, in_person_verified
    is_mfa_verified: bool = True
    granted_scopes: List[str] = Field(default_factory=list)
    issued_at: float = Field(default_factory=time.time)
    expires_at: float = Field(default_factory=lambda: time.time() + 3600)


class SecurityService:
    """Manages session tokens, zero-trust cryptographic validations, and CPNI enforcement."""

    def __init__(self, secret_key: str = settings.secret_key):
        self.secret_key = secret_key.encode("utf-8")

    def generate_session_token(self, subscriber_id: str, msisdn: str, auth_level: str = "authenticated_mfa") -> str:
        """Generate a cryptographically signed session token."""
        timestamp = str(int(time.time()))
        payload = f"{subscriber_id}:{msisdn}:{auth_level}:{timestamp}"
        signature = hmac.new(self.secret_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{payload}:{signature}"

    def verify_session_token(self, token: str) -> Optional[Tuple[str, str, str]]:
        """Verify the integrity and expiration of a session token."""
        try:
            parts = token.split(":")
            if len(parts) != 5:
                return None
            subscriber_id, msisdn, auth_level, timestamp_str, sig = parts
            payload = f"{subscriber_id}:{msisdn}:{auth_level}:{timestamp_str}"
            expected_sig = hmac.new(self.secret_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected_sig):
                return None
            issued_at = int(timestamp_str)
            if time.time() - issued_at > 7200:  # 2-hour validity
                return None
            return subscriber_id, msisdn, auth_level
        except Exception as e:
            logger.warning("Session token validation failed", error=str(e))
            return None

    def validate_cpni_access(self, auth_context: AuthContext, required_scope: str) -> Tuple[bool, str]:
        """Validate if the customer interaction satisfies CPNI authorization requirements."""
        if not settings.cpni_enclave_enabled:
            return True, "CPNI enclave disabled in development mode"

        # Check expiration
        if time.time() > auth_context.expires_at:
            return False, "CPNI Session expired. Re-authentication required."

        # High-risk operations (e.g., SIM Swap, PAC port-out) require strict step-up MFA
        if required_scope == CPNIScope.HIGH_RISK_CREDENTIAL:
            if auth_context.auth_level not in ["authenticated_mfa", "in_person_verified"]:
                return False, "Zero-Trust Enforcement: SIM/Credential modifications mandate out-of-band cryptographic Step-up MFA."

        if required_scope not in auth_context.granted_scopes:
            return False, f"CPNI Policy Violation: Missing required authorization scope '{required_scope}'"

        return True, "Authorized"

    def issue_default_auth_context(self, subscriber_id: str, msisdn: str, auth_level: str = "authenticated_mfa") -> AuthContext:
        """Issue default pre-populated authorization context for valid subscriber sessions."""
        scopes = [
            CPNIScope.VIEW_ACCOUNT,
            CPNIScope.VIEW_BILLING,
            CPNIScope.VIEW_CALL_RECORDS,
            CPNIScope.MODIFY_SERVICES,
            CPNIScope.FINANCIAL_CREDIT,
        ]
        if auth_level in ["authenticated_mfa", "in_person_verified"]:
            scopes.append(CPNIScope.HIGH_RISK_CREDENTIAL)

        return AuthContext(
            session_id=f"sess-{hashlib.md5(f'{subscriber_id}:{time.time()}'.encode()).hexdigest()[:10]}",
            subscriber_id=subscriber_id,
            msisdn=msisdn,
            auth_level=auth_level,
            is_mfa_verified=(auth_level in ["authenticated_mfa", "in_person_verified"]),
            granted_scopes=scopes,
        )


security_service = SecurityService()
