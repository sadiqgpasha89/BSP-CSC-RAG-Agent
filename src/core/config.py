"""Enterprise Configuration for Telco Agentic RAG & ML/NLP System."""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global system configuration including Hybrid Cloud (OpenStack + GCP) parameters."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # General Environment
    environment: str = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    secret_key: str = "telco-enterprise-agentic-rag-secret-key-super-secure-2026"

    # Hybrid Cloud: OpenStack (On-Premises Private Cloud)
    openstack_enabled: bool = True
    openstack_auth_url: str = "http://openstack-keystone.internal:5000/v3"
    openstack_vllm_endpoint: str = "http://openstack-vllm.internal:8000/v1"
    openstack_opensearch_endpoint: str = "http://openstack-opensearch.internal:9200"
    openstack_redis_endpoint: str = "redis://openstack-redis.internal:6379/0"
    openstack_baseline_cost_per_1k_tokens: float = 0.00012  # Amortized bare-metal GPU cost

    # Hybrid Cloud: Google Cloud Platform (Public Cloud Elastic Burst & Lakehouse)
    gcp_enabled: bool = True
    gcp_project_id: str = "telco-agentic-prod"
    gcp_region: str = "us-central1"
    gcp_vertex_ai_model: str = "gemini-1.5-flash"
    gcp_bigquery_dataset: str = "telco_lakehouse"
    gcp_pubsub_topic: str = "telco-telemetry-events"
    gcp_vertex_input_cost_per_1k: float = 0.00125
    gcp_vertex_output_cost_per_1k: float = 0.00375
    gcp_gke_burst_cost_per_1k: float = 0.00085

    # Thresholds & Limits (Strictly adhering to Architecture Spec)
    intent_confidence_threshold: float = 0.82  # tau_intent
    rag_relevance_threshold: float = 0.70      # tau_rag
    churn_risk_threshold: float = 0.75         # delta_churn
    max_agent_steps: int = 6                   # Deterministic loop cutoff (N <= 6)
    max_refund_autonomous: float = 50.0        # Max automated credit without supervisor
    max_refund_supervisor_required: float = 100.0  # > $100 requires HITL escalation

    # Drift Detection Thresholds
    drift_psi_threshold: float = 0.20          # PSI >= 0.2 triggers drift alert
    drift_ks_p_value_threshold: float = 0.05   # p-value < 0.05 indicates statistical drift
    rag_similarity_drift_threshold: float = 0.85 # Retrieval similarity drop below 85% baseline

    # Security & Compliance
    cpni_enclave_enabled: bool = True


settings = Settings()
