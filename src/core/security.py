"""Enterprise Security, CPNI Authorization & Zero-Trust Access Control."""

import hashlib
import hmac
import time
import uuid
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

MFA_LEVELS = ("authenticated_mfa", "in_person_verified")
KNOWN_AUTH_LEVELS = ("unauthenticated", "basic_pin") + MFA_LEVELS
TOKEN_TTL_SEC = 7200


class CPNIScope:
    """CPNI Access Scopes under Federal Communications Commission (FCC) Regulations."""
    VIEW_ACCOUNT = "cpni:view:account"
    VIEW_BILLING = "cpni:view:billing"
    VIEW_CALL_RECORDS = "cpni:view:cdr"
    MODIFY_SERVICES = "cpni:modify:services"
    HIGH_RISK_CREDENTIAL = "cpni:modify:credential_sim"
    FINANCIAL_CREDIT = "cpni:modify:billing_credit"


# Least-privilege scope matrix keyed by authentication assurance level.
SCOPES_BY_AUTH_LEVEL: Dict[str, List[str]] = {
    "unauthenticated": [],
    "basic_pin": [CPNIScope.VIEW_ACCOUNT, CPNIScope.VIEW_BILLING],
    "authenticated_mfa": [
        CPNIScope.VIEW_ACCOUNT,
        CPNIScope.VIEW_BILLING,
        CPNIScope.VIEW_CALL_RECORDS,
        CPNIScope.MODIFY_SERVICES,
        CPNIScope.FINANCIAL_CREDIT,
        CPNIScope.HIGH_RISK_CREDENTIAL,
    ],
}
SCOPES_BY_AUTH_LEVEL["in_person_verified"] = list(SCOPES_BY_AUTH_LEVEL["authenticated_mfa"])


class AuthContext(BaseModel):
    """Authenticated Customer Context and CPNI Scopes.

    Defaults are deliberately *fail-closed*: a context that was not explicitly elevated carries no MFA
    assurance and no scopes.
    """
    session_id: str
    subscriber_id: str
    msisdn: str
    auth_level: str = "unauthenticated"  # unauthenticated, basic_pin, authenticated_mfa, in_person_verified
    is_mfa_verified: bool = False
    granted_scopes: List[str] = Field(default_factory=list)
    issued_at: float = Field(default_factory=time.time)
    expires_at: float = Field(default_factory=lambda: time.time() + 3600)


class SecurityService:
    """Manages session tokens, zero-trust cryptographic validations, and CPNI enforcement."""

    def __init__(self, secret_key: Optional[str] = None):
        self.secret_key = (secret_key or settings.secret_key).encode("utf-8")

    # ------------------------------------------------------------------ tokens
    def _sign(self, payload: str) -> str:
        return hmac.new(self.secret_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def generate_session_token(self, subscriber_id: str, msisdn: str, auth_level: str = "authenticated_mfa") -> str:
        """Generate a cryptographically signed session token."""
        if auth_level not in KNOWN_AUTH_LEVELS:
            raise ValueError(f"Unknown auth level '{auth_level}'")
        if any(":" in part for part in (subscriber_id, msisdn, auth_level)):
            raise ValueError("Token fields must not contain the ':' delimiter")
        payload = f"{subscriber_id}:{msisdn}:{auth_level}:{int(time.time())}"
        return f"{payload}:{self._sign(payload)}"

    def verify_session_token(self, token: str) -> Optional[Tuple[str, str, str]]:
        """Verify the integrity and expiration of a session token."""
        try:
            parts = token.split(":")
            if len(parts) != 5:
                return None
            subscriber_id, msisdn, auth_level, timestamp_str, sig = parts
            payload = f"{subscriber_id}:{msisdn}:{auth_level}:{timestamp_str}"
            if not hmac.compare_digest(sig, self._sign(payload)):
                return None
            if auth_level not in KNOWN_AUTH_LEVELS:
                return None
            issued_at = int(timestamp_str)
            age = time.time() - issued_at
            if age > TOKEN_TTL_SEC or age < -60:  # expired, or minted in the future
                return None
            return subscriber_id, msisdn, auth_level
        except Exception as e:
            logger.warning("Session token validation failed", error=str(e))
            return None

    # --------------------------------------------------------------- authorization
    def validate_cpni_access(self, auth_context: Optional[AuthContext], required_scope: str) -> Tuple[bool, str]:
        """Validate if the customer interaction satisfies CPNI authorization requirements."""
        if not settings.cpni_enclave_enabled:
            return True, "CPNI enclave disabled in development mode"

        if auth_context is None:
            return False, "No authenticated session context available. Re-authentication required."

        if time.time() > auth_context.expires_at:
            return False, "CPNI Session expired. Re-authentication required."

        # High-risk operations (e.g., SIM Swap, PAC port-out) require strict step-up MFA
        if required_scope == CPNIScope.HIGH_RISK_CREDENTIAL:
            if auth_context.auth_level not in MFA_LEVELS or not auth_context.is_mfa_verified:
                return False, "Zero-Trust Enforcement: SIM/Credential modifications mandate out-of-band cryptographic Step-up MFA."

        if required_scope not in auth_context.granted_scopes:
            return False, f"CPNI Policy Violation: Missing required authorization scope '{required_scope}'"

        return True, "Authorized"

    def issue_default_auth_context(
        self, subscriber_id: str, msisdn: str, auth_level: Optional[str] = None
    ) -> AuthContext:
        """Issue an authorization context whose scopes are derived strictly from the assurance level."""
        level = auth_level or settings.default_auth_level
        if level not in KNOWN_AUTH_LEVELS:
            level = "unauthenticated"

        return AuthContext(
            session_id=f"sess-{uuid.uuid4().hex[:10]}",
            subscriber_id=subscriber_id,
            msisdn=msisdn,
            auth_level=level,
            is_mfa_verified=(level in MFA_LEVELS),
            granted_scopes=list(SCOPES_BY_AUTH_LEVEL[level]),
        )

    def resolve_auth_context(
        self, subscriber_id: str, msisdn: str, session_token: Optional[str] = None
    ) -> AuthContext:
        """Build the auth context for a turn.

        A valid, subscriber-bound signed token sets the assurance level; otherwise the environment default
        applies (fail-closed outside development). A token minted for a *different* subscriber is ignored.
        """
        if session_token:
            verified = self.verify_session_token(session_token)
            if verified and verified[0] == subscriber_id:
                return self.issue_default_auth_context(subscriber_id, msisdn, verified[2])
            logger.warning("Rejected invalid or mismatched session token", subscriber_id=subscriber_id)
            return self.issue_default_auth_context(subscriber_id, msisdn, "unauthenticated")
        return self.issue_default_auth_context(subscriber_id, msisdn)


security_service = SecurityService()
