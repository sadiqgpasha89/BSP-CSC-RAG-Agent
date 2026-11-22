"""Policy engine: the single authority on whether a proposed action may run.

LLMs and agents only *propose* actions. This engine, driven by an operator-owned policy pack
(``config/policies.json``), decides: allow, deny, or which control must be satisfied first (customer confirmation,
step-up verification, consent, human approval). It never trusts model output as authorisation: every input here
(auth level, MFA flag, amounts, confirmation state) is established server-side by the gateway.
"""

import copy
import json
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.core.audit import audit_log
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

AUTH_RANK = {"unauthenticated": 0, "basic_pin": 1, "authenticated_mfa": 2, "in_person_verified": 3}

# Guard rails an admin edit may never relax (plan §8 / §12). Enforced in ``PolicyEngine.update_action``.
IMMUTABLE_TRUE = {
    "sim.replace": ["human_approval", "step_up", "confirmation"],
    "esim.issue": ["human_approval", "step_up", "confirmation"],
    "port.initiate": ["human_approval", "step_up", "confirmation"],
    "billing.credit": ["human_approval"],
    "network.change": ["forbidden"],
    "payment.initiate": ["confirmation"],
    "plan.change": ["confirmation"],
    "addon.change": ["confirmation"],
    "profile.update": ["confirmation"],
}


class Effect(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    ESCALATE = "ESCALATE"                              # beyond what automation may do: hand to a human
    REQUIRE_STEP_UP = "REQUIRE_STEP_UP"
    REQUIRE_CONSENT = "REQUIRE_CONSENT"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    REQUIRE_HUMAN_APPROVAL = "REQUIRE_HUMAN_APPROVAL"


class PolicyRequest(BaseModel):
    action: str
    agent: str
    subscriber_id: str
    auth_level: str = "unauthenticated"
    is_mfa_verified: bool = False
    step_up_verified: bool = False                     # fresh step-up inside the conversation
    amount: Optional[float] = None
    confirmed: bool = False                            # set ONLY by the gateway after a valid confirmation
    consent_granted: Optional[bool] = None
    human_approved_by: Optional[str] = None            # set ONLY by the operator approval path
    recent_total_amount: float = 0.0                   # same-subscriber payments in the last 24h
    recent_attempts: int = 0                           # same-subscriber attempts in the last hour


class PolicyDecision(BaseModel):
    effect: Effect
    action: str
    reasons: List[str] = Field(default_factory=list)
    policy_version: str = ""
    risk: str = "unknown"

    @property
    def allowed(self) -> bool:
        return self.effect == Effect.ALLOW


class PolicyEngine:
    def __init__(self, path: Optional[str] = None):
        self._path = Path(path or settings.policy_file)
        self._lock = threading.RLock()
        self._pack: Dict[str, Any] = {}
        self._baseline: Dict[str, Any] = {}
        self.reload()

    # ----------------------------------------------------------------- loading
    def reload(self) -> None:
        with self._lock:
            try:
                self._pack = json.loads(self._path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                logger.error("Policy pack missing: failing closed (deny all)", path=str(self._path))
                self._pack = {"version": "missing", "actions": {}, "agent_permissions": {}, "defaults": {"deny_unknown_actions": True}}
            self._baseline = copy.deepcopy(self._pack)

    @property
    def version(self) -> str:
        return str(self._pack.get("version", "unknown"))

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._pack)

    def section(self, name: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self._pack.get(name, default))

    def action_rule(self, action: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            rule = self._pack.get("actions", {}).get(action)
            return copy.deepcopy(rule) if rule is not None else None

    def agent_may(self, agent: str, action: str) -> bool:
        with self._lock:
            return action in self._pack.get("agent_permissions", {}).get(agent, [])

    # --------------------------------------------------------------- evaluation
    def evaluate(self, req: PolicyRequest) -> PolicyDecision:
        rule = self.action_rule(req.action)
        ver = self.version

        def decide(effect: Effect, *reasons: str) -> PolicyDecision:
            return PolicyDecision(effect=effect, action=req.action, reasons=list(reasons), policy_version=ver,
                                  risk=(rule or {}).get("risk", "unknown"))

        if rule is None:
            return decide(Effect.DENY, f"Action '{req.action}' is not defined in the policy pack (deny by default).")
        if rule.get("forbidden"):
            return decide(Effect.DENY, rule.get("forbidden_reason", "Action is forbidden by policy."))
        if not self.agent_may(req.agent, req.action):
            return decide(Effect.DENY, f"Agent '{req.agent}' is not permitted to perform '{req.action}' (least privilege).")

        # 1. Authentication assurance.
        required = rule.get("min_auth", "basic_pin")
        have = AUTH_RANK.get(req.auth_level, 0)
        need = AUTH_RANK.get(required, 1)
        mfa_ok = req.is_mfa_verified or req.step_up_verified
        if have < need:
            if need >= AUTH_RANK["authenticated_mfa"] and have >= AUTH_RANK["basic_pin"] and not mfa_ok:
                return decide(Effect.REQUIRE_STEP_UP, f"'{req.action}' requires step-up verification (assurance '{required}').")
            if not (need >= AUTH_RANK["authenticated_mfa"] and mfa_ok):
                return decide(Effect.DENY, f"Authentication level '{req.auth_level}' is below the required '{required}'.")

        # 2. Value limits.
        if req.amount is not None:
            max_amount = rule.get("max_amount")
            if max_amount is not None and req.amount > float(max_amount):
                return decide(Effect.ESCALATE, f"Amount {req.amount:.2f} exceeds the automated limit {float(max_amount):.2f}.")
            daily = rule.get("daily_limit")
            if daily is not None and req.recent_total_amount + req.amount > float(daily):
                return decide(Effect.ESCALATE, f"Daily limit {float(daily):.2f} would be exceeded.")
            if req.action == "billing.credit":
                ceiling = float(rule.get("hard_ceiling", 0.0))
                if req.amount > ceiling:
                    return decide(Effect.ESCALATE, f"Credit {req.amount:.2f} exceeds the hard ceiling {ceiling:.2f}.")
            if req.action == "offer.apply":
                cap = rule.get("max_value_without_approval")
                if cap is not None and req.amount > float(cap) and not req.human_approved_by:
                    return decide(Effect.REQUIRE_HUMAN_APPROVAL, f"Offer value {req.amount:.2f} exceeds {float(cap):.2f}: supervisor approval required.")
        max_attempts = rule.get("max_attempts_per_hour")
        if max_attempts is not None and req.recent_attempts >= int(max_attempts):
            return decide(Effect.ESCALATE, "Too many attempts in the last hour; routed to a human for fraud review.")

        # 3. Fresh step-up for critical actions (MFA at login is not enough for SIM/eSIM/port).
        if rule.get("step_up") and not req.step_up_verified:
            return decide(Effect.REQUIRE_STEP_UP, f"'{req.action}' always requires a fresh step-up verification.")

        # 4. Consent.
        purpose = rule.get("consent")
        if purpose and req.consent_granted is not True:
            return decide(Effect.REQUIRE_CONSENT, f"Customer consent for '{purpose}' is required first.")

        # 5. Explicit customer confirmation.
        if rule.get("confirmation") and not req.confirmed:
            return decide(Effect.REQUIRE_CONFIRMATION, "Explicit customer confirmation is required.")

        # 6. Human approval (the action is only ever executed by an operator-approved path).
        if rule.get("human_approval") and not req.human_approved_by:
            return decide(Effect.REQUIRE_HUMAN_APPROVAL, "Human approval is required; automation may only prepare this request.")

        return decide(Effect.ALLOW, "All policy conditions satisfied.")

    # ------------------------------------------------------------------- admin
    def update_action(self, action: str, patch: Dict[str, Any], actor: str) -> Dict[str, Any]:
        """Apply an admin edit to one action rule, refusing any change that would relax a protected guard rail."""
        with self._lock:
            rules = self._pack.setdefault("actions", {})
            if action not in rules:
                raise KeyError(action)
            candidate = {**rules[action], **patch}
            for key in IMMUTABLE_TRUE.get(action, []):
                if candidate.get(key) is not True:
                    raise PermissionError(f"Policy guard rail: '{key}' on '{action}' cannot be disabled.")
            if action == "billing.credit":
                if float(candidate.get("autonomous_limit", 0.0)) > 0.0:
                    raise PermissionError("Policy guard rail: autonomous credits are not allowed (plan §12).")
            for key, val in patch.items():
                if key.endswith("_limit") or key.startswith("max_"):
                    if not isinstance(val, (int, float)) or val < 0:
                        raise ValueError(f"'{key}' must be a non-negative number")
            before = rules[action]
            rules[action] = candidate
            self._pack["version"] = f"{self._baseline.get('version', '0')}+admin.{int(time.time())}"
        audit_log.append(
            "POLICY_CHANGE", correlation_id=f"admin-{int(time.time())}", action=f"update:{action}", outcome="APPLIED",
            actor=actor, details={"before": before, "after": candidate, "version": self.version},
        )
        return candidate

    def set_agent_permissions(self, agent: str, actions: List[str], actor: str) -> List[str]:
        with self._lock:
            known = set(self._pack.get("actions", {}))
            unknown = [a for a in actions if a not in known]
            if unknown:
                raise ValueError(f"Unknown actions: {unknown}")
            forbidden = [a for a in actions if self._pack["actions"][a].get("forbidden")]
            if forbidden:
                raise PermissionError(f"Forbidden actions cannot be granted: {forbidden}")
            before = self._pack.setdefault("agent_permissions", {}).get(agent, [])
            self._pack["agent_permissions"][agent] = list(actions)
        audit_log.append("POLICY_CHANGE", correlation_id=f"admin-{int(time.time())}", action=f"agent_permissions:{agent}",
                         outcome="APPLIED", actor=actor, details={"before": before, "after": actions})
        return list(actions)

    def reset_to_baseline(self) -> None:
        with self._lock:
            self._pack = copy.deepcopy(self._baseline)


policy_engine = PolicyEngine()
