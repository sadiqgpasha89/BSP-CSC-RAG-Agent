"""Unit Tests for 6-Category Unified Metrics Engine."""

from src.metrics.engine import metrics_engine


def test_metrics_snapshot_all_categories():
    snapshot = metrics_engine.get_snapshot()

    # Category 1: Business
    assert snapshot.business.first_contact_resolution_pct >= 80.0
    assert snapshot.business.average_handling_time_sec > 0.0
    assert snapshot.business.csat_score >= 4.0

    # Category 2: System
    assert snapshot.system.time_to_first_token_ms <= 350.0  # Voice SLA
    assert snapshot.system.p95_latency_ms < 600.0

    # Category 3: RAG
    assert snapshot.rag.faithfulness_score >= 0.85
    assert snapshot.rag.context_precision >= 0.80

    # Category 4: Agentic
    assert snapshot.agentic.agent_path_efficiency >= 0.80
    assert snapshot.agentic.tool_invocation_success_rate_pct >= 90.0

    # Category 5: ML / NLP
    assert snapshot.ml_nlp.intent_classification_macro_f1 >= 0.90
    assert snapshot.ml_nlp.asr_word_error_rate_wer < 0.10

    # Category 6: Cost Arbitrage
    assert snapshot.cost_arbitrage.savings_percentage >= 0.0
