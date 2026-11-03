"""Network Operations Center (NOC) & Diagnostics API Router."""

from typing import Dict, Any, Optional
from fastapi import APIRouter
from pydantic import BaseModel
from src.data_engineering.graph_rag_builder import graph_builder
from src.data_engineering.synthetic_data_generator import data_generator
from src.tools.oss_adapter import oss_adapter, OTDRTestResult

router = APIRouter(prefix="/network", tags=["Network Operations & Telemetry"])


class OTDRRequest(BaseModel):
    olt_port: str = "OLT-OPTICAL-01/PON-04"
    subscriber_id: str = "SUB-882194"


class CoreResetRequest(BaseModel):
    msisdn: str = "+1-415-555-0199"


@router.get("/topology")
def get_network_topology() -> Dict[str, Any]:
    """Return all nodes and relational edges of network infrastructure."""
    subgraph = graph_builder.query_subgraph("ROUTER-BH-01", depth=3)
    return subgraph


@router.get("/alarms")
def get_active_alarms():
    """Return real-time active network alarms."""
    return data_generator.load_network_alarms()


@router.post("/otdr-test", response_model=OTDRTestResult)
def trigger_otdr_test(req: OTDRRequest):
    """Trigger simulated OTDR optical line pulse test."""
    return oss_adapter.run_otdr_fiber_test(olt_port=req.olt_port, subscriber_id=req.subscriber_id)


@router.post("/core-reset")
def trigger_core_reset(req: CoreResetRequest):
    """Execute signaling soft reset on MSISDN in UDM/HSS core."""
    return oss_adapter.execute_core_soft_reset(msisdn=req.msisdn)
