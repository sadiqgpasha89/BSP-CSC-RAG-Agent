"""Omnichannel Conversational API Router."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.deps import require_operator
from src.agents.supervisor import SubscriberNotFoundError, SupervisorExecutionResponse, supervisor_agent
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["Omnichannel Chat & Agent Execution"])


class ChatMessageRequest(BaseModel):
    # No default subscriber: omitting the field used to silently act on Alex Mercer's account.
    subscriber_id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9+\-_.]+$")
    message: str = Field(min_length=1, max_length=settings.max_message_chars)
    session_id: Optional[str] = Field(default=None, max_length=64)
    historical_sentiments: List[float] = Field(default_factory=list, max_length=50)
    is_audio: bool = False
    session_token: Optional[str] = Field(default=None, max_length=512, description="Signed session token carrying the MFA assurance level")
    channel: str = Field(default="web", pattern=r"^(web|mobile|whatsapp|voice|contact_centre)$")
    language_hint: Optional[str] = Field(default=None, pattern=r"^[a-z]{2}$")


# Plain ``def`` (not ``async def``): the pipeline is synchronous and CPU/IO blocking, so FastAPI must run it in its
# worker thread pool. As an ``async def`` handler it blocked the event loop for the duration of every turn.
@router.post("/message", response_model=SupervisorExecutionResponse)
def process_chat_message(request: ChatMessageRequest):
    """Ingest user message, run 8-stage agentic resolution pipeline, and return synthesized response."""
    try:
        return supervisor_agent.process_turn(
            user_utterance=request.message,
            subscriber_id=request.subscriber_id,
            session_id=request.session_id,
            historical_sentiments=[max(-1.0, min(1.0, v)) for v in request.historical_sentiments],
            is_audio=request.is_audio,
            session_token=request.session_token,
            channel=request.channel,
            language_hint=request.language_hint,
        )
    except SubscriberNotFoundError:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Session belongs to a different customer")
    except Exception as e:
        logger.error("Chat endpoint processing failed", error=repr(e))
        raise HTTPException(status_code=500, detail="Internal agentic error")  # details stay in the server log


class ControlRequest(BaseModel):
    subscriber_id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9+\-_.]+$")
    session_id: str = Field(min_length=1, max_length=64)
    session_token: Optional[str] = Field(default=None, max_length=512)


def _control(request: ControlRequest, word: str):
    # Buttons use exactly the same path as typing YES / NO: the control flow is the only thing that can release an action.
    try:
        return supervisor_agent.process_turn(word, request.subscriber_id, session_id=request.session_id, session_token=request.session_token)
    except SubscriberNotFoundError:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Session belongs to a different customer")


@router.post("/confirm", response_model=SupervisorExecutionResponse)
def confirm_pending(request: ControlRequest):
    """Customer confirms the action currently awaiting confirmation in this session."""
    return _control(request, "yes")


@router.post("/cancel", response_model=SupervisorExecutionResponse)
def cancel_pending(request: ControlRequest):
    return _control(request, "no")


class CsatRequest(ControlRequest):
    rating: int = Field(ge=1, le=5)
    resolved: Optional[bool] = None


@router.post("/csat")
def submit_csat(request: CsatRequest):
    from src.core.conversation import conversation_store
    conv = conversation_store.get(request.session_id)
    if conv is None or conv.subscriber_id != request.subscriber_id:
        raise HTTPException(status_code=404, detail="Session not found")
    conv.csat, conv.resolved = request.rating, request.resolved
    return {"status": "recorded"}


@router.get("/{session_id}/messages")
def human_messages(session_id: str, subscriber_id: str, after: float = 0.0, session_token: Optional[str] = None):
    """Poll for replies from a human agent after handover. The caller must prove the session is theirs."""
    from src.core.conversation import conversation_store
    from src.core.security import security_service
    from src.hitl.warm_handoff import warm_handoff_manager
    conv = conversation_store.get(session_id)
    verified = security_service.verify_session_token(session_token) if session_token else None
    if conv is None or conv.subscriber_id != subscriber_id or (not settings.is_development and (not verified or verified[0] != subscriber_id)):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"handover_state": conv.handover_state, "messages": warm_handoff_manager.messages_for_session(session_id, after)}


@router.get("/subscribers", dependencies=[Depends(require_operator)])
def list_subscribers():
    """Return mock enterprise subscribers for demonstration and testing (operator only: contains MSISDNs/CPNI)."""
    from src.data_engineering.synthetic_data_generator import data_generator
    return data_generator.load_subscribers()
