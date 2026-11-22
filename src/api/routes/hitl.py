"""Human-In-The-Loop (HITL) Queue Management API Router."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.deps import require_operator
from src.hitl.warm_handoff import EscalationAlreadyResolvedError, WarmHandoffManifest, warm_handoff_manager

# The queue holds MSISDNs, CLV and case summaries: every endpoint here is operator-only.
router = APIRouter(prefix="/hitl", tags=["HITL Escalations & Warm Handoff"], dependencies=[Depends(require_operator)])


class ResolveEscalationRequest(BaseModel):
    escalation_id: str = Field(min_length=1, max_length=64)
    resolution_notes: str = Field(min_length=1, max_length=2000)


@router.get("/queue", response_model=List[WarmHandoffManifest])
def get_escalation_queue():
    """List pending warm handoff escalation dossiers."""
    return warm_handoff_manager.get_pending_queue()


@router.post("/resolve")
def resolve_escalation(req: ResolveEscalationRequest):
    """Mark an escalation as resolved by representative, persisting the audit notes."""
    try:
        res = warm_handoff_manager.resolve_escalation(req.escalation_id, req.resolution_notes)
    except EscalationAlreadyResolvedError:
        raise HTTPException(status_code=409, detail="Escalation already resolved")
    if not res:
        raise HTTPException(status_code=404, detail="Escalation ID not found")
    return {"status": "resolved", "escalation_id": req.escalation_id, "resolved_at": res.resolved_at}


class ClaimRequest(BaseModel):
    escalation_id: str = Field(min_length=1, max_length=64)
    operator: str = Field(min_length=1, max_length=64)


class ReplyRequest(ClaimRequest):
    text: str = Field(min_length=1, max_length=1500)


@router.post("/claim")
def claim_escalation(req: ClaimRequest):
    """A human agent takes over the conversation; the customer then talks to them, not the assistant."""
    try:
        m = warm_handoff_manager.claim(req.escalation_id, req.operator)
    except EscalationAlreadyResolvedError:
        raise HTTPException(status_code=409, detail="Escalation already claimed or resolved")
    if not m:
        raise HTTPException(status_code=404, detail="Escalation ID not found")
    return {"status": "claimed", "summary": m.summary, "suggested_next_action": m.suggested_next_action, "awaiting_approval": m.awaiting_approval, "messages": m.messages}


@router.post("/reply")
def reply_to_customer(req: ReplyRequest):
    msg = warm_handoff_manager.reply(req.escalation_id, req.operator, req.text)
    if not msg:
        raise HTTPException(status_code=409, detail="Claim the escalation first")
    return {"status": "sent", "ts": msg["ts"]}
