"""Operator approval queue for human-approval actions (SIM/eSIM/port, over-limit offers). Never reachable by an agent."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.deps import require_operator
from src.core.confirmations import pending_actions
from src.tools.gateway import ToolStatus, tool_gateway

router = APIRouter(prefix="/approvals", tags=["Human Approvals"], dependencies=[Depends(require_operator)])


class DecisionRequest(BaseModel):
    operator: str = Field(min_length=1, max_length=64)
    note: Optional[str] = Field(default=None, max_length=500)


@router.get("")
def list_awaiting():
    return [{"action_id": a.action_id, "tool": a.tool, "summary": a.summary, "risk": a.risk, "subscriber_id": a.subscriber_id, "created_at": a.created_at,
             "confirmed_at": a.confirmed_at} for a in pending_actions.awaiting_approval()]


def _decide(action_id: str, req: DecisionRequest, approve: bool):
    res = tool_gateway.approve(action_id, req.operator, approve=approve, note=req.note or "")
    if res.status == ToolStatus.NOT_FOUND:
        raise HTTPException(status_code=404, detail="No such request awaiting approval")
    return {"status": res.status.value, "message": res.message, "data": res.data, "error_code": res.error_code}


@router.post("/{action_id}/approve")
def approve(action_id: str, req: DecisionRequest):
    return _decide(action_id, req, True)


@router.post("/{action_id}/reject")
def reject(action_id: str, req: DecisionRequest):
    return _decide(action_id, req, False)
