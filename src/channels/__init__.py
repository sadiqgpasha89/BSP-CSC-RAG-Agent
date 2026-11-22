"""Channel adapters: normalise inbound channel payloads to one supervisor turn and shape replies per channel.

All channels run the SAME agent pipeline and the SAME policy engine. A channel only changes how text arrives and how
the reply is formatted (length, markup, buttons), never what the customer is allowed to do.
"""

import hashlib
import hmac
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.agents.supervisor import SupervisorExecutionResponse, supervisor_agent
from src.core.config import settings

CHANNEL_LIMITS = {"web": 4000, "mobile": 2000, "whatsapp": 1500, "voice": 500, "contact_centre": 4000}


class InboundMessage(BaseModel):
    channel: str
    subscriber_id: str
    session_id: str
    text: str
    is_audio: bool = False
    language_hint: Optional[str] = None
    session_token: Optional[str] = None
    external_id: Optional[str] = None


class OutboundMessage(BaseModel):
    channel: str
    session_id: str
    text: str
    buttons: List[str] = Field(default_factory=list)
    pending_action_id: Optional[str] = None
    handover_id: Optional[str] = None
    escalated: bool = False
    language: str = "en"
    meta: Dict[str, Any] = Field(default_factory=dict)


def _strip_markup(text: str) -> str:
    return re.sub(r"[*_`#>]", "", text)


def format_reply(channel: str, res: SupervisorExecutionResponse) -> OutboundMessage:
    text = res.final_response.strip()
    buttons: List[str] = []
    if res.pending_action and res.pending_action.get("status") == "CONFIRMATION_REQUIRED":
        buttons = ["YES", "NO"]
    if channel == "voice":
        text = _strip_markup(text).replace("\n", ". ")
        text = re.sub(r"\b(TCK|PAY|ORD|ESC|INV)-[A-Z0-9\-]+", lambda m: " ".join(m.group(0)), text)   # spell references out for TTS
        text = re.sub(r"\s+", " ", text)
    elif channel == "whatsapp":
        text = _strip_markup(text)
    limit = CHANNEL_LIMITS.get(channel, 2000)
    if len(text) > limit:
        text = text[: limit - 40].rsplit(" ", 1)[0] + "... (reply MORE for the rest)"
    return OutboundMessage(channel=channel, session_id=res.session_id, text=text, buttons=buttons,
                           pending_action_id=(res.pending_action or {}).get("action_id"), handover_id=res.handover_id,
                           escalated=res.escalated_to_human, language=res.language,
                           meta={"trace_id": res.trace_id, "requires_step_up": res.requires_step_up})


def handle(msg: InboundMessage) -> OutboundMessage:
    res = supervisor_agent.process_turn(user_utterance=msg.text, subscriber_id=msg.subscriber_id, session_id=msg.session_id, is_audio=msg.is_audio,
                                        session_token=msg.session_token, channel=msg.channel, language_hint=msg.language_hint)
    return format_reply(msg.channel, res)


# ---------------------------------------------------------------------------------------------- WhatsApp
def verify_whatsapp_signature(body: bytes, signature_header: Optional[str]) -> bool:
    """X-Hub-Signature-256 check. With no app secret configured the webhook is refused outside development."""
    secret = settings.whatsapp_app_secret
    if not secret:
        return settings.is_development
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.split("=", 1)[1])


def _phone_to_subscriber(wa_id: str) -> Optional[str]:
    from src.data_engineering.synthetic_data_generator import data_generator
    digits = re.sub(r"\D", "", wa_id)
    for s in data_generator.load_subscribers():
        if re.sub(r"\D", "", s["msisdn"]) == digits:
            return s["subscriber_id"]
    return None


def parse_whatsapp(payload: Dict[str, Any]) -> List[InboundMessage]:
    """Extract text messages from a WhatsApp Cloud API webhook body. A number is only the *claimed* identity: the
    session starts at the lowest assurance (no token), so account actions still need PIN / OTP in the conversation."""
    out: List[InboundMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for m in change.get("value", {}).get("messages", []):
                if m.get("type") != "text":
                    continue
                sub = _phone_to_subscriber(m.get("from", ""))
                if not sub:
                    continue
                out.append(InboundMessage(channel="whatsapp", subscriber_id=sub, session_id=f"wa-{m['from']}", text=m["text"]["body"][:2000], external_id=m.get("id")))
    return out


# ---------------------------------------------------------------------------------------------- voice / contact centre
def parse_voice(payload: Dict[str, Any]) -> InboundMessage:
    """Speech-to-text result from the telephony layer (ASR runs upstream; the transcript goes through the same pipeline)."""
    return InboundMessage(channel="voice", subscriber_id=payload["subscriber_id"], session_id=payload.get("call_id", "call-unknown"),
                          text=payload["transcript"][:1000], is_audio=True, language_hint=payload.get("language"))


def parse_contact_centre(payload: Dict[str, Any]) -> InboundMessage:
    """Assist mode: the contact-centre desktop forwards the customer's words and shows the assistant's draft to the human."""
    return InboundMessage(channel="contact_centre", subscriber_id=payload["subscriber_id"], session_id=payload.get("interaction_id", "cc-unknown"),
                          text=payload["customer_text"][:2000], session_token=payload.get("session_token"))
