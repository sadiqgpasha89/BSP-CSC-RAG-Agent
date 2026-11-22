"""Operator administration: policy pack, audit trail, KPIs, proactive notices."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.deps import require_operator
from src.core.audit import audit_log
from src.core.i18n import coverage
from src.core.policy import policy_engine
from src.core.proactive import outage_notifier
from src.metrics.kpi import kpi_service

router = APIRouter(prefix="/admin", tags=["Administration"], dependencies=[Depends(require_operator)])


class PolicyPatch(BaseModel):
    actor: str = Field(min_length=1, max_length=64)
    patch: Dict[str, Any]


class PermissionPatch(BaseModel):
    actor: str = Field(min_length=1, max_length=64)
    actions: List[str]


@router.get("/policy")
def get_policy():
    return {"version": policy_engine.version, "policy": policy_engine.snapshot()}


@router.patch("/policy/actions/{action}")
def patch_action(action: str, body: PolicyPatch):
    try:
        return policy_engine.update_action(action, body.patch, body.actor)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown action")
    except PermissionError as exc:           # guard rails admins cannot relax
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.put("/policy/agents/{agent}")
def put_permissions(agent: str, body: PermissionPatch):
    try:
        return {"agent": agent, "actions": policy_engine.set_agent_permissions(agent, body.actions, body.actor)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/audit")
def query_audit(correlation_id: Optional[str] = None, session_id: Optional[str] = None, subscriber_id: Optional[str] = None,
                event_type: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500)):
    return [r.model_dump() for r in audit_log.query(correlation_id=correlation_id, session_id=session_id, subscriber_id=subscriber_id, event_type=event_type, limit=limit)]


@router.get("/audit/verify")
def verify_audit():
    return audit_log.verify_chain()


@router.get("/kpis")
def kpis():
    return kpi_service.compute()


@router.get("/i18n/coverage")
def i18n_coverage():
    return coverage()


class RestoreRequest(BaseModel):
    alarm_id: str = Field(min_length=1, max_length=64)


@router.post("/outage/restore")
def outage_restored(req: RestoreRequest):
    """Send the 'service restored' notice to everyone who subscribed to this incident (consent-aware)."""
    return {"notified": outage_notifier.restore(req.alarm_id)}
