"""Human-In-The-Loop (HITL) Queue Management API Router."""

from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from src.hitl.warm_handoff import warm_handoff_manager, WarmHandoffManifest

router = APIRouter(prefix="/hitl", tags=["HITL Escalations & Warm Handoff"])


class ResolveEscalationRequest(BaseModel):
    escalation_id: str
    resolution_notes: str


@router.get("/queue", response_model=List[WarmHandoffManifest])
def get_escalation_queue():
    """List pending warm handoff escalation dossiers."""
    return warm_handoff_manager.get_pending_queue()


@router.post("/resolve")
def resolve_escalation(req: ResolveEscalationRequest):
    """Mark an escalation as resolved by representative."""
    res = warm_handoff_manager.resolve_escalation(req.escalation_id, req.resolution_notes)
    if not res:
        raise HTTPException(status_code=404, detail="Escalation ID not found")
    return {"status": "resolved", "escalation_id": req.escalation_id}
