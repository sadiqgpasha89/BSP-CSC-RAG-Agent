"""Pytest Configuration and Shared Test Fixtures."""

import pytest
from src.agents.state import AgenticSessionState
from src.core.security import security_service


@pytest.fixture(autouse=True)
def _reset_shared_global_state():
    """Singletons are process-wide; undo mutations (e.g. self-healing rebalancing) so tests are order-independent."""
    from src.rag.hybrid_retriever import hybrid_retriever

    hybrid_retriever.dense_weight = hybrid_retriever.bm25_weight = hybrid_retriever.graph_weight = 1.0
    _reset_platform_state()
    yield
    hybrid_retriever.dense_weight = hybrid_retriever.bm25_weight = hybrid_retriever.graph_weight = 1.0
    _reset_platform_state()


def _reset_platform_state():
    """Sandbox adapters, conversations, confirmations, consent and audit are process-wide: start every test clean."""
    from src.core.audit import audit_log
    from src.core.auth import auth_service
    from src.core.confirmations import idempotency_store, pending_actions
    from src.core.consent import consent_service
    from src.core.conversation import conversation_store
    from src.core.policy import policy_engine
    from src.core.proactive import outage_notifier
    from src.tools.bss_adapter import bss_adapter
    from src.tools.crm_adapter import crm_adapter
    from src.tools.gateway import tool_gateway
    from src.tools.notification_adapter import notification_adapter
    from src.tools.ocs_adapter import ocs_adapter
    from src.tools.payment_adapter import payment_adapter
    from src.tools.ticketing_adapter import ticketing_adapter

    for svc in (audit_log, auth_service, pending_actions, idempotency_store, consent_service, conversation_store, outage_notifier, crm_adapter,
                notification_adapter, ocs_adapter, payment_adapter, ticketing_adapter):
        svc.reset()
    from src.data_engineering.stream_processor import stream_processor
    stream_processor.clear_buffer()
    bss_adapter.reset_invoices()
    policy_engine.reset_to_baseline()
    tool_gateway._breakers.clear()


@pytest.fixture
def test_client():
    """Create synchronous FastAPI TestClient (imported lazily so domain tests do not need the web stack)."""
    from starlette.testclient import TestClient
    from src.api.app import app

    return TestClient(app)


@pytest.fixture
def sample_auth_context():
    """Create verified sample authorization context."""
    return security_service.issue_default_auth_context(
        subscriber_id="SUB-882194",
        msisdn="+1-415-555-0199",
        auth_level="authenticated_mfa",
    )


@pytest.fixture
def sample_session_state(sample_auth_context):
    """Create fresh sample session state for agent testing."""
    return AgenticSessionState(
        session_id="test-sess-001",
        trace_id="test-trc-001",
        subscriber_id="SUB-882194",
        auth_context=sample_auth_context,
        raw_utterance="Test utterance",
        blackboard={
            "verified_msisdn": "+1-415-555-0199",
            "subscriber_tier": "enterprise_platinum",
            "subscriber_plan_code": "5G_ULTRA_MAX_2026",
            "current_tower_id": "TOWER-104",
            "assigned_sector": "Sector-7",
            "clv_usd": 3150.00,
            "disputed_amount": 30.00,
        },
    )
