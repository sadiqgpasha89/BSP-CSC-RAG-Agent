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
        )
    except SubscriberNotFoundError:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    except Exception as e:
        logger.error("Chat endpoint processing failed", error=repr(e))
        raise HTTPException(status_code=500, detail="Internal agentic error")  # details stay in the server log


@router.get("/subscribers", dependencies=[Depends(require_operator)])
def list_subscribers():
    """Return mock enterprise subscribers for demonstration and testing (operator only: contains MSISDNs/CPNI)."""
    from src.data_engineering.synthetic_data_generator import data_generator
    return data_generator.load_subscribers()
