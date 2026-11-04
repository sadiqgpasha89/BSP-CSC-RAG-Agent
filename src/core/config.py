"""Enterprise Configuration for Telco Agentic RAG & ML/NLP System."""

from pathlib import Path
from typing import List, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root (…/src/core/config.py -> repo root). Anchoring every on-disk path here makes
# the service independent of the process working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Sentinel used only for local development; startup fails fast outside development if it is not overridden.
INSECURE_DEV_SECRET = "dev-only-insecure-secret-change-me"

DEV_ENVIRONMENTS = {"development", "dev", "test", "local"}


class Settings(BaseSettings):
    """Global system configuration including Hybrid Cloud (OpenStack + GCP) parameters."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # General Environment
    environment: str = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    secret_key: str = INSECURE_DEV_SECRET
    data_dir: str = str(PROJECT_ROOT / "data")

    # API hardening
    cors_allowed_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    admin_api_key: Optional[str] = None  # Required (X-API-Key) for operator / state-changing endpoints outside development
    max_message_chars: int = 2000

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
    frustration_velocity_threshold: float = -0.5  # d(sentiment)/dt below this triggers proactive handoff
    max_agent_steps: int = 6                   # Deterministic loop cutoff (N <= 6)
    max_refund_autonomous: float = 50.0        # Max automated credit without supervisor
    max_refund_supervisor_required: float = 100.0  # > $100 requires HITL escalation

    # Drift Detection Thresholds
    drift_psi_threshold: float = 0.20          # PSI >= 0.2 triggers drift alert
    drift_ks_p_value_threshold: float = 0.05   # p-value < 0.05 indicates statistical drift
    rag_similarity_drift_threshold: float = 0.85 # Retrieval similarity drop below 85% baseline

    # Security & Compliance
    cpni_enclave_enabled: bool = True

    @property
    def is_development(self) -> bool:
        return self.environment.strip().lower() in DEV_ENVIRONMENTS

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def default_auth_level(self) -> str:
        """Auth level granted to a session that presents no verified token.

        Development keeps the demo console usable (it has no login flow). Every other environment
        fails closed: callers must present a signed session token to obtain MFA-level privileges.
        """
        return "authenticated_mfa" if self.is_development else "basic_pin"

    @model_validator(mode="after")
    def _enforce_production_secrets(self) -> "Settings":
        if not self.is_development:
            if self.secret_key == INSECURE_DEV_SECRET or len(self.secret_key) < 32:
                raise ValueError(
                    "SECRET_KEY must be overridden with a random value of >= 32 characters when ENVIRONMENT is not development."
                )
            if not self.admin_api_key:
                raise ValueError("ADMIN_API_KEY must be set when ENVIRONMENT is not development.")
        return self


settings = Settings()
