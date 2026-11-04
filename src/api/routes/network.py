"""Network Operations Center (NOC) & Diagnostics API Router."""

from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.api.deps import require_operator
from src.data_engineering.graph_rag_builder import graph_builder
from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.oss_adapter import oss_adapter, OTDRTestResult

router = APIRouter(prefix="/network", tags=["Network Operations & Telemetry"])


class OTDRRequest(BaseModel):
    olt_port: str = Field(default="OLT-OPTICAL-01/PON-04", pattern=r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*$", max_length=64)
    subscriber_id: str = Field(default="SUB-882194", pattern=r"^[A-Za-z0-9+\-_.]+$", max_length=64)


class CoreResetRequest(BaseModel):
    msisdn: str = Field(default="+1-415-555-0199", pattern=r"^\+?[0-9][0-9\- ]{6,19}$")


@router.get("/topology")
def get_network_topology() -> Dict[str, Any]:
    """Return all nodes and relational edges of network infrastructure."""
    return graph_builder.export_full_graph()


@router.get("/alarms")
def get_active_alarms():
    """Return real-time active network alarms."""
    return data_generator.load_network_alarms()


@router.post("/otdr-test", response_model=OTDRTestResult, dependencies=[Depends(require_operator)])
def trigger_otdr_test(req: OTDRRequest):
    """Trigger simulated OTDR optical line pulse test."""
    return oss_adapter.run_otdr_fiber_test(olt_port=req.olt_port, subscriber_id=req.subscriber_id)


@router.post("/core-reset", dependencies=[Depends(require_operator)])
def trigger_core_reset(req: CoreResetRequest):
    """Execute signaling soft reset on MSISDN in UDM/HSS core."""
    return oss_adapter.execute_core_soft_reset(msisdn=req.msisdn)
