"""Identity platform (sandbox): login, session tokens and step-up (one-time-code) verification.

Production replaces this with the operator's OAuth 2.0 / OpenID Connect provider and an OTP/push service behind the same
interface. The properties that matter and are enforced here:

* login yields only ``basic_pin`` assurance; MFA-level privileges come exclusively from a verified step-up;
* step-up codes are CSPRNG-generated, stored only as salted hashes, single-use, expiring, attempt-limited and
  rate-limited per subscriber; they are never written to logs or the audit trail;
* a lock-out after repeated failures forces a human path.
"""

import hashlib
import hmac
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

from pydantic import BaseModel

from src.core.audit import audit_log
from src.core.config import settings
from src.core.conversation import Conversation
from src.core.security import security_service
from src.data_engineering.synthetic_data_generator import data_generator

MAX_LOGIN_FAILURES = 5
LOGIN_LOCK_SEC = 900
MAX_CHALLENGES_PER_HOUR = 3


class LoginResult(BaseModel):
    success: bool
    subscriber_id: Optional[str] = None
    session_token: Optional[str] = None
    auth_level: str = "unauthenticated"
    message: str = ""


class StepUpResult(BaseModel):
    status: str                      # VERIFIED | BAD_CODE | LOCKED | EXPIRED | NO_CHALLENGE
    attempts_left: int = 0
    elevated_until: float = 0.0


def hash_pin(subscriber_id: str, pin: str) -> str:
    return hashlib.sha256(f"{subscriber_id}:{pin}".encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self):
        self._lock = threading.RLock()
        self._failures: Dict[str, Tuple[int, float]] = {}
        self._challenges: Dict[Tuple[str, str], Dict] = {}
        self._issued: Dict[str, list] = {}

    # ------------------------------------------------------------------ login
    def login(self, identifier: str, pin: str, correlation_id: str) -> LoginResult:
        sub = data_generator.get_subscriber(identifier)
        key = sub["subscriber_id"] if sub else f"unknown:{identifier[:12]}"
        with self._lock:
            fails, since = self._failures.get(key, (0, 0.0))
            if fails >= MAX_LOGIN_FAILURES and time.time() - since < LOGIN_LOCK_SEC:
                audit_log.append("IDENTITY_CHECK", correlation_id, "login", "LOCKED", subscriber_id=key)
                return LoginResult(success=False, message="Too many attempts. Please try again later or contact support.")
        # Same generic message and comparable work whether or not the account exists (no account enumeration).
        if sub:
            ok = hmac.compare_digest(hash_pin(sub["subscriber_id"], pin), sub.get("sandbox_pin_hash", "0" * 64))
        else:
            hmac.compare_digest(hash_pin("unknown", pin), "0" * 64)
            ok = False
        if not ok:
            with self._lock:
                fails, _ = self._failures.get(key, (0, 0.0))
                self._failures[key] = (fails + 1, time.time())
            audit_log.append("IDENTITY_CHECK", correlation_id, "login", "FAILED", subscriber_id=key if sub else None)
            return LoginResult(success=False, message="Those details don't match our records.")
        with self._lock:
            self._failures.pop(key, None)
        token = security_service.generate_session_token(sub["subscriber_id"], sub["msisdn"], "basic_pin")
        audit_log.append("IDENTITY_CHECK", correlation_id, "login", "SUCCESS", subscriber_id=sub["subscriber_id"],
                         details={"auth_level": "basic_pin"})
        return LoginResult(success=True, subscriber_id=sub["subscriber_id"], session_token=token, auth_level="basic_pin", message="Signed in.")

    # ---------------------------------------------------------------- step-up
    def start_step_up(self, conv: Conversation, correlation_id: str) -> Dict:
        from src.tools.notification_adapter import notification_adapter   # local import: adapters depend on core

        sid = conv.subscriber_id
        now = time.time()
        with self._lock:
            recent = [t for t in self._issued.get(sid, []) if now - t < 3600]
            if len(recent) >= MAX_CHALLENGES_PER_HOUR:
                audit_log.append("IDENTITY_CHECK", correlation_id, "step_up_start", "RATE_LIMITED", session_id=conv.session_id, subscriber_id=sid)
                return {"sent": False, "reason": "RATE_LIMITED"}
            code = f"{secrets.randbelow(10**6):06d}"
            salt = secrets.token_hex(8)
            self._challenges[(sid, conv.session_id)] = {
                "salt": salt, "hash": hashlib.sha256((salt + code).encode()).hexdigest(),
                "expires": now + settings.stepup_otp_ttl_sec, "attempts_left": settings.stepup_max_attempts}
            recent.append(now)
            self._issued[sid] = recent
        receipt = notification_adapter.send(sid, "otp", "otp_message", {"code": code, "minutes": settings.stepup_otp_ttl_sec // 60},
                                            language=conv.language, sandbox_visible={"otp": code})
        audit_log.append("IDENTITY_CHECK", correlation_id, "step_up_start", "SENT" if receipt.get("delivered") else "NOT_DELIVERED",
                         session_id=conv.session_id, subscriber_id=sid, details={"channel": receipt.get("channel")})
        return {"sent": bool(receipt.get("delivered")), "channel": receipt.get("channel")}

    def verify_step_up(self, conv: Conversation, code: str, correlation_id: str) -> StepUpResult:
        key = (conv.subscriber_id, conv.session_id)
        with self._lock:
            ch = self._challenges.get(key)
            if ch is None:
                return StepUpResult(status="NO_CHALLENGE")
            if time.time() > ch["expires"]:
                del self._challenges[key]
                audit_log.append("IDENTITY_CHECK", correlation_id, "step_up_verify", "EXPIRED", session_id=conv.session_id, subscriber_id=conv.subscriber_id)
                return StepUpResult(status="EXPIRED")
            if ch["attempts_left"] <= 0:
                return StepUpResult(status="LOCKED")
            good = hmac.compare_digest(hashlib.sha256((ch["salt"] + code.strip()).encode()).hexdigest(), ch["hash"])
            if good:
                del self._challenges[key]
                conv.stepup_verified_until = time.time() + settings.stepup_elevation_ttl_sec
                audit_log.append("IDENTITY_CHECK", correlation_id, "step_up_verify", "VERIFIED", session_id=conv.session_id, subscriber_id=conv.subscriber_id)
                return StepUpResult(status="VERIFIED", elevated_until=conv.stepup_verified_until)
            ch["attempts_left"] -= 1
            left = ch["attempts_left"]
            audit_log.append("IDENTITY_CHECK", correlation_id, "step_up_verify", "BAD_CODE" if left else "LOCKED",
                             session_id=conv.session_id, subscriber_id=conv.subscriber_id)
            return StepUpResult(status="BAD_CODE" if left else "LOCKED", attempts_left=left)

    def has_open_challenge(self, conv: Conversation) -> bool:
        with self._lock:
            ch = self._challenges.get((conv.subscriber_id, conv.session_id))
            return bool(ch and time.time() <= ch["expires"] and ch["attempts_left"] > 0)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._challenges.clear()
            self._issued.clear()


auth_service = AuthService()
