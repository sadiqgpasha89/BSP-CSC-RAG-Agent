"""Unit Tests for OSS/BSS Enterprise Tool Adapters & Circuit Breakers."""

from src.tools.bss_adapter import bss_adapter
from src.tools.oss_adapter import oss_adapter
from src.tools.provisioning_adapter import provisioning_adapter
from src.tools.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from src.core.security import security_service


def test_bss_credit_autonomous_threshold():
    # <= $50 auto approved
    res = bss_adapter.apply_billing_credit(
        subscriber_id="SUB-882194",
        amount_usd=30.00,
        reason="Disputed international roaming charge",
    )
    assert res.success is True
    assert res.authorization_level == "AUTONOMOUS"
    assert res.credited_amount_usd == 30.00


def test_bss_credit_supervisor_required_threshold():
    # > $50 and <= $100 requires supervisor
    res = bss_adapter.apply_billing_credit(
        subscriber_id="SUB-882194",
        amount_usd=75.00,
        reason="Service outage goodwill concession",
    )
    assert res.success is True
    assert res.authorization_level == "SUPERVISOR_OVERRIDE"


def test_bss_credit_hard_ceiling_escalation():
    # > $100 must be rejected and escalated to human
    res = bss_adapter.apply_billing_credit(
        subscriber_id="SUB-882194",
        amount_usd=150.00,
        reason="Large claim",
    )
    assert res.success is False
    assert res.authorization_level == "ESCALATION_REQUIRED"


def test_oss_otdr_fiber_cut_detection():
    res = oss_adapter.run_otdr_fiber_test(
        olt_port="OLT-OPTICAL-01/PON-04",
        subscriber_id="SUB-449182",
    )
    assert res.line_status == "FIBER_CUT_DETECTED"
    assert res.fault_distance_meters is not None
    assert res.technician_dispatch_needed is True


def test_oss_core_soft_reset():
    res = oss_adapter.execute_core_soft_reset(msisdn="+1-415-555-0199")
    assert res["success"] is True
    assert res["action"] == "UDM_HSS_CORE_PDP_RESET"


def test_provisioning_zero_trust_sim_swap():
    # Unverified auth context must be rejected
    unverified_auth = security_service.issue_default_auth_context(
        subscriber_id="SUB-882194",
        msisdn="+1-415-555-0199",
        auth_level="unauthenticated",
    )
    unverified_auth.is_mfa_verified = False

    res = provisioning_adapter.validate_sim_swap_request(
        subscriber_id="SUB-882194",
        requested_iccid="8901410329998881112",
        auth_context=unverified_auth,
    )
    assert res.success is False
    assert "Zero-Trust Requirement" in res.message


def test_circuit_breaker_tripping():
    cb = CircuitBreaker("test_circuit", CircuitBreakerConfig(failure_threshold=2, recovery_timeout_sec=1.0))
    assert cb.state == CircuitState.CLOSED

    def failing_func():
        raise ConnectionError("OSS Timeout")

    # Call 1: failure
    try:
        cb.call(failing_func)
    except ConnectionError:
        pass
    assert cb.state == CircuitState.CLOSED

    # Call 2: failure trips circuit
    try:
        cb.call(failing_func)
    except ConnectionError:
        pass
    assert cb.state == CircuitState.OPEN
