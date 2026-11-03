"""Edge Case Test: Cyclic Agentic Loop Detection & Termination (Section 10.4)."""

from src.agents.state import AgenticSessionState


def test_cyclic_loop_detection_trigger():
    state = AgenticSessionState(
        session_id="loop-sess-001",
        trace_id="loop-trc-001",
        subscriber_id="SUB-882194",
        agent_delegation_history=["BillingAgent", "NetworkDiagnosticAgent", "BillingAgent", "NetworkDiagnosticAgent"],
    )

    assert state.detect_cyclic_loop() is True


def test_max_steps_cutoff_policy():
    state = AgenticSessionState(
        session_id="loop-sess-002",
        trace_id="loop-trc-002",
        subscriber_id="SUB-882194",
        step_count=6,
    )
    from src.core.config import settings
    assert state.step_count >= settings.max_agent_steps
