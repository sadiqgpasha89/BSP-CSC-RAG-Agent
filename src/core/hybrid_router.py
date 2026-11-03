"""Hybrid Cloud Workload Placement & Cost Arbitrage Engine (OpenStack + GCP)."""

import time
from enum import Enum
from typing import Dict, Any, Tuple
from pydantic import BaseModel
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


class CloudProvider(str, Enum):
    OPENSTACK = "openstack_onprem"
    GCP_VERTEX = "gcp_vertex_ai"
    GCP_GKE_BURST = "gcp_gke_burst"


class ExecutionRoutingDecision(BaseModel):
    provider: CloudProvider
    model_name: str
    target_endpoint: str
    rationale: str
    estimated_latency_ms: float
    estimated_cost_usd: float
    cost_savings_vs_cloud_usd: float


class HybridCloudRouter:
    """Intelligently routes inference and tool tasks between On-Premises OpenStack and Google Cloud Platform."""

    def __init__(self):
        self.openstack_baseline_cost = settings.openstack_baseline_cost_per_1k_tokens
        self.gcp_vertex_input_cost = settings.gcp_vertex_input_cost_per_1k
        self.gcp_vertex_output_cost = settings.gcp_vertex_output_cost_per_1k
        self.gcp_gke_cost = settings.gcp_gke_burst_cost_per_1k

        # Aggregated Telemetry Counters
        self.total_openstack_routed: int = 0
        self.total_gcp_vertex_routed: int = 0
        self.total_gcp_burst_routed: int = 0
        self.cumulative_savings_usd: float = 0.0

    def route_inference(
        self,
        task_type: str,
        input_tokens: int,
        output_tokens: int,
        is_multimodal: bool = False,
        is_surge: bool = False,
        is_high_complexity: bool = False,
    ) -> ExecutionRoutingDecision:
        """Determine optimal execution provider maximizing throughput, minimizing latency and token cost."""
        total_tokens = input_tokens + output_tokens

        # 1. Multi-modal queries (e.g., router LED photos) or extreme multi-intent synthesis -> GCP Vertex AI
        if is_multimodal or (is_high_complexity and not is_surge):
            provider = CloudProvider.GCP_VERTEX
            model_name = settings.gcp_vertex_ai_model
            target_endpoint = f"https://{settings.gcp_region}-aiplatform.googleapis.com/v1"
            rationale = "Frontier reasoning / multi-modal diagnostics assigned to GCP Vertex AI"
            est_latency = 320.0
            cost = (input_tokens / 1000.0 * self.gcp_vertex_input_cost) + (output_tokens / 1000.0 * self.gcp_vertex_output_cost)
            savings = 0.0
            self.total_gcp_vertex_routed += 1

        # 2. Localized network outage surge (> 400% baseline traffic) -> GCP GKE Autopilot Burst
        elif is_surge:
            provider = CloudProvider.GCP_GKE_BURST
            model_name = "mistral-70b-instruct-gke-autoscale"
            target_endpoint = f"https://gke-gateway.{settings.gcp_region}.internal:8443/v1"
            rationale = "Traffic surge detected; cloud burst execution via GCP GKE Autopilot"
            est_latency = 180.0
            cost = (total_tokens / 1000.0) * self.gcp_gke_cost
            benchmark_cloud_cost = (input_tokens / 1000.0 * self.gcp_vertex_input_cost) + (output_tokens / 1000.0 * self.gcp_vertex_output_cost)
            savings = max(0.0, benchmark_cloud_cost - cost)
            self.total_gcp_burst_routed += 1
            self.cumulative_savings_usd += savings

        # 3. Predictable baseline conversational queries & worker sub-agents -> OpenStack on-premises vLLM
        else:
            provider = CloudProvider.OPENSTACK
            model_name = "llama-3.1-70b-instruct-vllm-int4"
            target_endpoint = settings.openstack_vllm_endpoint
            rationale = "Predictable baseline query routed to OpenStack on-premises vLLM (Zero variable token cost)"
            est_latency = 95.0
            cost = (total_tokens / 1000.0) * self.openstack_baseline_cost
            benchmark_cloud_cost = (input_tokens / 1000.0 * self.gcp_vertex_input_cost) + (output_tokens / 1000.0 * self.gcp_vertex_output_cost)
            savings = max(0.0, benchmark_cloud_cost - cost)
            self.total_openstack_routed += 1
            self.cumulative_savings_usd += savings

        decision = ExecutionRoutingDecision(
            provider=provider,
            model_name=model_name,
            target_endpoint=target_endpoint,
            rationale=rationale,
            estimated_latency_ms=est_latency,
            estimated_cost_usd=round(cost, 6),
            cost_savings_vs_cloud_usd=round(savings, 6),
        )

        logger.debug("Inference routing resolved", provider=provider.value, cost_usd=decision.estimated_cost_usd)
        return decision

    def get_arbitrage_telemetry(self) -> Dict[str, Any]:
        """Return real-time hybrid cloud routing metrics and cost arbitrage savings."""
        total_invocations = self.total_openstack_routed + self.total_gcp_vertex_routed + self.total_gcp_burst_routed
        openstack_pct = (self.total_openstack_routed / total_invocations * 100.0) if total_invocations > 0 else 0.0
        gcp_pct = ((self.total_gcp_vertex_routed + self.total_gcp_burst_routed) / total_invocations * 100.0) if total_invocations > 0 else 0.0

        return {
            "total_routed_requests": total_invocations,
            "openstack_onprem_count": self.total_openstack_routed,
            "openstack_percentage": round(openstack_pct, 2),
            "gcp_vertex_count": self.total_gcp_vertex_routed,
            "gcp_burst_count": self.total_gcp_burst_routed,
            "gcp_total_percentage": round(gcp_pct, 2),
            "cumulative_arbitrage_savings_usd": round(self.cumulative_savings_usd, 4),
            "openstack_baseline_cost_per_1k": self.openstack_baseline_cost,
            "gcp_vertex_input_cost_per_1k": self.gcp_vertex_input_cost,
        }


hybrid_router = HybridCloudRouter()
