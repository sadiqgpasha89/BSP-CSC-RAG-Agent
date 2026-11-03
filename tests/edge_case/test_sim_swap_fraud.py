"""Edge Case Test: Fraud & Social Engineering SIM-Swap Mitigation (Section 10.2)."""

from src.tools.provisioning_adapter import provisioning_adapter
from src.core.security import security_service


def test_sim_swap_social_engineering_blocked():
    auth_ctx = security_service.issue_default_auth_context(
        subscriber_id="SUB-773019",
        msisdn="+1-415-555-0466",
        auth_level="authenticated_mfa",
    )

    # Attacker utilizes emergency emotional framing
    res = provisioning_adapter.validate_sim_swap_request(
        subscriber_id="SUB-773019",
        requested_iccid="8901410329998881112",
        auth_context=auth_ctx,
        emotional_urgency_detected=True,
    )

    assert res.success is False
    assert "Anti-Fraud Safeguard" in res.message
    assert "retail branch" in res.message or "Step-Up MFA" in res.message
