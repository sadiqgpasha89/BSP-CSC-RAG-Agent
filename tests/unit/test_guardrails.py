"""Unit Tests for Deterministic Policy & Guardrails Layer."""

from src.agents.guardrails import guardrails_service


def test_guardrail_factuality_pass():
    context = [{"content": "Under the 2026 5G Ultra Max plan, mobile hotspot is 50GB per month."}]
    response = "Your plan includes 50GB of high-speed mobile hotspot per month."
    verdict = guardrails_service.validate(response, context)
    assert verdict.passed is True
    assert verdict.faithfulness_score >= 0.70


def test_guardrail_unauthorized_promise_block():
    context = [{"content": "Monthly billing cycle is 30 days."}]
    response = "Don't worry, we will waive your entire bill forever!"
    verdict = guardrails_service.validate(response, context)
    assert verdict.passed is False
    assert any("Policy Violation" in v for v in verdict.violations)


def test_guardrail_cpni_leak_prevention():
    context = [{"content": "Customer billing details"}]
    response = "Your credit card number on file is 4532 1234 5678 8910."
    verdict = guardrails_service.validate(response, context)
    assert verdict.passed is False
    assert any("CPNI Compliance Breach" in v for v in verdict.violations)
