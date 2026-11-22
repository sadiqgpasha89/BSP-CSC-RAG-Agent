"""Conversation control flow: confirmations, OTP step-up, consent replies and resumption.

These are the turns where the customer is answering *the system* (YES / NO / a one-time code / "I agree") rather than
asking something new. They are handled deterministically and never reach the language model, so a model cannot
confirm, cancel, or verify on the customer's behalf.
"""

import re
import unicodedata
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.agents.state import AgenticSessionState
from src.core import i18n
from src.core.auth import auth_service
from src.core.config import settings
from src.core.confirmations import ActionStatus, pending_actions
from src.core.consent import consent_service
from src.core.conversation import Conversation
from src.core.security import security_service
from src.tools.gateway import ToolContext, ToolResult, ToolStatus, tool_gateway
from src.workflows.payments import complete_payment

YES = {"yes", "y", "yep", "yeah", "confirm", "confirmed", "ok", "okay", "sure", "proceed", "go ahead", "do it", "yes please", "please do",
       "si", "sí", "confirmo", "dale", "adelante", "claro", "हाँ", "हां", "ठीक है", "कन्फर्म", "जी हाँ", "जी हां"}
NO = {"no", "n", "nope", "cancel", "stop", "don't", "dont", "never mind", "nevermind", "abort", "no thanks",
      "cancela", "cancelar", "no gracias", "nahi", "नहीं", "नही", "ना", "रद्द", "रद्द करें", "मत करो"}
AGREE = re.compile(r"^\W*(i agree|i consent|yes[, ]+i (?:agree|consent)|agree|acepto|मैं सहमत हूँ|सहमत)\W*$", re.I)
OTP_RE = re.compile(r"^\D{0,12}(\d{6})\D{0,12}$")


class ControlResult(BaseModel):
    message: str
    intent: str
    escalate_reason: Optional[str] = None
    pending: Optional[Dict[str, Any]] = None
    requires_step_up: bool = False
    transaction_refs: List[str] = Field(default_factory=list)


def _norm(text: str) -> str:
    """Lower-case and drop punctuation/symbols but keep letters and combining marks (Devanagari vowel signs are marks, not \\w)."""
    kept = "".join(ch for ch in text.lower() if ch in "' " or unicodedata.category(ch)[0] in ("L", "M", "N"))
    return re.sub(r"\s+", " ", kept).strip()


def classify_reply(text: str) -> Optional[str]:
    n = _norm(text)
    if n in YES:
        return "YES"
    if n in NO:
        return "NO"
    return None


def build_auth(conv: Conversation, subscriber_id: str, msisdn: str, token: Optional[str]):
    """Resolve the auth context; an OTP step-up that is still valid lifts a PIN-level session to MFA assurance."""
    auth = security_service.resolve_auth_context(subscriber_id=subscriber_id, msisdn=msisdn, session_token=token)
    if conv.step_up_active and auth.auth_level == "basic_pin":
        auth = security_service.issue_default_auth_context(subscriber_id, msisdn, "authenticated_mfa")
    return auth


def _ctx(state: AgenticSessionState, conv: Conversation, agent: str) -> ToolContext:
    return ToolContext(subscriber_id=state.subscriber_id, session_id=state.session_id, trace_id=state.trace_id, agent=agent,
                       auth_context=state.auth_context, step_up_verified=conv.step_up_active, channel=conv.channel, language=conv.language)


def start_step_up_message(state: AgenticSessionState, conv: Conversation) -> str:
    sent = auth_service.start_step_up(conv, state.trace_id)
    if not sent.get("sent"):
        return i18n.render("system_unavailable", conv.language)
    return i18n.render("stepup_required", conv.language)


def describe_result(tool: str, d: Dict[str, Any]) -> str:
    """Customer-facing success text, built only from the system's own response."""
    if tool == "ticket.create":
        txt = f"Your case {d['case_id']} is registered (priority {d['priority']}); you can expect a response within {d['sla_hours']} hours."
        return txt + (" You already had a similar open case, so I linked this to it instead of creating a duplicate." if d.get("deduplicated") else "")
    if tool == "billing.register_dispute":
        c = d["case"]
        return (f"Your billing dispute is registered as case {c['case_id']} with the evidence attached. A billing specialist will review it within {c['sla_hours']} hours. "
                "No credit has been applied yet; that is the specialist's decision.")
    if tool == "plan.change":
        if not d.get("verified_by_readback"):
            return "I submitted the plan change but could not verify it in the account yet, so I won't say it is done. A specialist will check."
        from src.tools.catalog_adapter import catalog_adapter
        return f"Done. Your plan is now {catalog_adapter.get_plan(d['plan_code'])['name']} at ${d['new_monthly_fee_usd']:.2f}/month, effective {d['effective']} (ref {d['change_ref']})."
    if tool == "addon.change":
        if not d.get("verified_by_readback"):
            return "I submitted the add-on change but could not verify it yet, so I won't say it is done. A specialist will check."
        from src.tools.catalog_adapter import catalog_adapter
        return f"Done. {catalog_adapter.get_addon(d['addon_code'])['name']} is now {'active' if d['active'] else 'removed'} on your line (ref {d['change_ref']})."
    if tool == "offer.apply":
        return f"I've applied the {d['title']} (value ${d['value_usd']:.2f}), reference {d['offer_ref']}."
    if tool == "profile.update":
        return f"I've updated your {d['field']}. We also sent a notice to your previous contact for security."
    if tool == "preferences.update":
        return "Your contact preferences are updated."
    if tool == "network.soft_reset":
        return "I refreshed your line's network registration. Please restart your phone, and tell me if it is still not working."
    if tool in ("sim.replace", "esim.issue", "port.initiate"):
        return "Your request has been approved and is being processed."
    return "Done."


def apply_pending_result(state: AgenticSessionState, conv: Conversation, act_tool: str, res: ToolResult) -> ControlResult:
    lang = conv.language
    refs = [r for r in [(res.data or {}).get("record_id")] if r]
    if act_tool == "payment.initiate":
        msgs, esc, pay_id = complete_payment(_ctx(state, conv, "BillingAgent"), res)
        if pay_id:
            conv.notes["last_payment_id"] = pay_id
            refs.append(pay_id)
        return ControlResult(message=" ".join(msgs), intent="PAYMENT", escalate_reason=esc, transaction_refs=refs)
    st = res.status
    if st == ToolStatus.OK:
        return ControlResult(message=describe_result(act_tool, res.data), intent="CONFIRMED_ACTION", transaction_refs=refs)
    if st == ToolStatus.APPROVAL_REQUIRED:
        return ControlResult(message="Thank you. This request needs approval from a specialist before it can go ahead. I've submitted it and you'll be notified of the outcome. "
                                      f"Reference: {res.pending_action_id}.", intent="AWAITING_APPROVAL", transaction_refs=[res.pending_action_id or ""])
    if st == ToolStatus.OUTCOME_UNKNOWN:
        return ControlResult(message="The system did not confirm the result, so I can't tell you it worked. I have NOT repeated the action; a specialist will check it.",
                             intent="OUTCOME_UNKNOWN", escalate_reason=f"{act_tool} outcome unknown")
    if st == ToolStatus.UNAVAILABLE:
        return ControlResult(message=i18n.render("system_unavailable", lang), intent="SYSTEM_UNAVAILABLE", escalate_reason=f"{act_tool} backend unavailable")
    if st == ToolStatus.ESCALATE:
        return ControlResult(message="This needs a specialist's decision, so I have passed it to one.", intent="ESCALATED", escalate_reason=res.message)
    return ControlResult(message=res.message or "I couldn't complete that. Nothing was changed.", intent="ACTION_FAILED")


class ControlFlow:
    def try_handle(self, state: AgenticSessionState, conv: Conversation, utterance: str) -> Optional[ControlResult]:
        lang = conv.language
        open_actions = [a for a in pending_actions.open_for_session(conv.session_id, conv.subscriber_id) if a.status == ActionStatus.PENDING_CONFIRMATION]

        # 1. One-time code
        m = OTP_RE.match(utterance.strip())
        if m:
            if auth_service.has_open_challenge(conv):
                return self._otp(state, conv, m.group(1))
            return ControlResult(message="I'm not waiting for a code right now. What would you like to do?", intent="NO_CHALLENGE")

        # 2. Consent reply
        awaiting = conv.notes.get("awaiting_consent")
        if awaiting and AGREE.match(utterance):
            consent_service.record_consent(conv.subscriber_id, awaiting["purpose"], True, correlation_id=state.trace_id)
            conv.notes.pop("awaiting_consent", None)
            return self._resume(state, conv, prefix="Thank you, I've recorded your permission.")

        # 3. YES / NO on the latest pending action
        verdict = classify_reply(utterance)
        if verdict and open_actions:
            act = sorted(open_actions, key=lambda a: a.created_at)[-1]
            if verdict == "NO":
                tool_gateway.cancel(_ctx(state, conv, act.agent), act.action_id)
                return ControlResult(message=i18n.render("confirm_cancelled", lang), intent="CONFIRMATION_CANCELLED")
            return self._confirm(state, conv, act.action_id, act.tool, act.agent)
        if verdict == "YES" and not open_actions:
            return ControlResult(message="There's nothing waiting for your confirmation. What would you like to do?", intent="NO_PENDING_ACTION")
        return None

    # ----------------------------------------------------------------------------------------------
    def _confirm(self, state, conv, action_id: str, tool: str, agent: str) -> ControlResult:
        res = tool_gateway.confirm(_ctx(state, conv, agent), action_id)
        if res.status == ToolStatus.STEP_UP_REQUIRED:
            conv.notes["resume_confirm"] = action_id
            return ControlResult(message=start_step_up_message(state, conv), intent="STEP_UP_REQUESTED", requires_step_up=True)
        if res.status == ToolStatus.FAILED and res.error_code == "EXPIRED":
            return ControlResult(message=i18n.render("confirm_expired", conv.language), intent="CONFIRMATION_EXPIRED")
        if res.status == ToolStatus.NOT_FOUND:
            return ControlResult(message="I couldn't find that request any more. Tell me again what you'd like to do.", intent="CONFIRMATION_NOT_FOUND")
        return apply_pending_result(state, conv, tool, res)

    def _otp(self, state, conv, code: str) -> ControlResult:
        lang = conv.language
        r = auth_service.verify_step_up(conv, code, state.trace_id)
        if r.status == "VERIFIED":
            minutes = settings.stepup_elevation_ttl_sec // 60
            conv.notes["step_up_just_verified"] = True
            if state.auth_context is not None and state.auth_context.auth_level == "basic_pin":
                state.auth_context = security_service.issue_default_auth_context(state.subscriber_id, state.auth_context.msisdn, "authenticated_mfa")
            state.blackboard["step_up_verified"] = True
            if "resume_confirm" in conv.notes:
                aid = conv.notes.pop("resume_confirm")
                act = pending_actions.get(aid)
                if act is not None and act.status == ActionStatus.PENDING_CONFIRMATION:
                    out = self._confirm(state, conv, aid, act.tool, act.agent)
                    out.message = i18n.render("stepup_ok", lang, minutes=minutes) + " " + out.message
                    return out
            return self._resume(state, conv, prefix=i18n.render("stepup_ok", lang, minutes=minutes))
        if r.status == "BAD_CODE":
            return ControlResult(message=i18n.render("stepup_bad", lang, left=r.attempts_left), intent="STEP_UP_FAILED")
        if r.status == "LOCKED":
            return ControlResult(message=i18n.render("stepup_locked", lang), intent="STEP_UP_LOCKED", escalate_reason="Step-up verification locked after repeated wrong codes")
        if r.status == "EXPIRED":
            return ControlResult(message="That code has expired. Ask me again and I'll send a new one.", intent="STEP_UP_EXPIRED")
        return ControlResult(message="I'm not waiting for a code right now.", intent="NO_CHALLENGE")

    def _resume(self, state, conv, prefix: str = "") -> ControlResult:
        """Re-issue the request that was waiting on step-up/consent. It still needs the customer's confirmation."""
        req = conv.notes.pop("resume_request", None)
        if not req:
            return ControlResult(message=prefix or i18n.render("anything_else", conv.language), intent="CONTROL_ACK")
        ctx = _ctx(state, conv, req["agent"])
        res = tool_gateway.invoke(ctx, req["tool"], req["args"])
        if res.status in (ToolStatus.CONFIRMATION_REQUIRED, ToolStatus.APPROVAL_REQUIRED):
            prompt = i18n.render("confirm_prompt", conv.language, summary=res.pending_summary, minutes=settings.confirmation_ttl_sec // 60)
            return ControlResult(message=f"{prefix} {prompt}".strip(), intent="CONFIRMATION_REQUESTED",
                                 pending={"action_id": res.pending_action_id, "summary": res.pending_summary, "expires_at": res.pending_expires_at})
        if res.status == ToolStatus.CONSENT_REQUIRED:
            conv.notes["resume_request"] = req
            conv.notes["awaiting_consent"] = {"purpose": "case_processing"}
            return ControlResult(message=f"{prefix} To proceed I need your permission to process this request. Reply \"I agree\" to allow it.".strip(), intent="CONSENT_REQUESTED")
        if res.ok:
            return ControlResult(message=f"{prefix} {describe_result(req['tool'], res.data)}".strip(), intent="CONFIRMED_ACTION")
        return ControlResult(message=f"{prefix} {res.message or 'I could not continue with that request.'}".strip(), intent="ACTION_FAILED")


control_flow = ControlFlow()
