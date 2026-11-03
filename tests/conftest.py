"""Pytest Configuration and Shared Test Fixtures."""

import pytest
from starlette.testclient import TestClient
from src.api.app import app
from src.agents.state import AgenticSessionState
from src.core.security import security_service


@pytest.fixture
def test_client():
    """Create synchronous FastAPI TestClient."""
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
