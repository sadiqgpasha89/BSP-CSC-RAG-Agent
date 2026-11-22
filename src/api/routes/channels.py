"""Channel entry points: WhatsApp webhook, voice transcripts, contact-centre assist. All use the same agent pipeline."""

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from src.channels import OutboundMessage, format_reply, handle, parse_contact_centre, parse_voice, parse_whatsapp, verify_whatsapp_signature
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/channels", tags=["Channels"])


@router.get("/whatsapp", response_class=PlainTextResponse)
def whatsapp_verify(hub_mode: str = Query(alias="hub.mode", default=""), hub_verify_token: str = Query(alias="hub.verify_token", default=""),
                    hub_challenge: str = Query(alias="hub.challenge", default="")):
    import hmac
    if hub_mode == "subscribe" and settings.whatsapp_verify_token and hmac.compare_digest(hub_verify_token, settings.whatsapp_verify_token):
        return hub_challenge
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, x_hub_signature_256: Optional[str] = Header(default=None)):
    body = await request.body()
    if not verify_whatsapp_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed body")
    replies = []
    for msg in parse_whatsapp(payload):
        try:
            replies.append(handle(msg).model_dump())
        except Exception as exc:       # never let one message poison the webhook (the platform would retry forever)
            logger.error("WhatsApp turn failed", error=repr(exc))
    return {"replies": replies}


@router.post("/voice", response_model=OutboundMessage)
def voice_turn(payload: Dict[str, Any]):
    try:
        return handle(parse_voice(payload))
    except KeyError:
        raise HTTPException(status_code=422, detail="subscriber_id and transcript are required")


@router.post("/contact-centre", response_model=OutboundMessage)
def contact_centre_assist(payload: Dict[str, Any]):
    try:
        return handle(parse_contact_centre(payload))
    except KeyError:
        raise HTTPException(status_code=422, detail="subscriber_id and customer_text are required")
