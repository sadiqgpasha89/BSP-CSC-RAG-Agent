"""Omnichannel Conversational API Router."""

from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from src.agents.supervisor import supervisor_agent, SupervisorExecutionResponse
from src.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["Omnichannel Chat & Agent Execution"])


class ChatMessageRequest(BaseModel):
    subscriber_id: str = "SUB-882194"
    message: str
    session_id: Optional[str] = None
    historical_sentiments: List[float] = Field(default_factory=list)
    is_audio: bool = False


@router.post("/message", response_model=SupervisorExecutionResponse)
async def process_chat_message(request: ChatMessageRequest):
    """Ingest user message, run 8-stage agentic resolution pipeline, and return synthesized response."""
    try:
        response = supervisor_agent.process_turn(
            user_utterance=request.message,
            subscriber_id=request.subscriber_id,
            session_id=request.session_id,
            historical_sentiments=request.historical_sentiments,
            is_audio=request.is_audio,
        )
        return response
    except Exception as e:
        logger.error("Chat endpoint processing failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Internal agentic error: {str(e)}")


@router.get("/subscribers")
async def list_subscribers():
    """Return all mock enterprise subscribers for demonstration and testing."""
    from src.data_engineering.synthetic_data_generator import data_generator
    return data_generator.load_subscribers()
