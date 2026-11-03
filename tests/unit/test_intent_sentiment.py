"""Unit Tests for Intent Classification, Sentiment Velocity & Churn Risk."""

from src.datascience.intent_classifier import intent_classifier
from src.datascience.sentiment_analyzer import sentiment_analyzer
from src.datascience.churn_predictor import churn_predictor


def test_intent_classification_single():
    text = "Why was I charged twice for my bill this month? I need a refund."
    res = intent_classifier.classify(text)
    assert res.primary_intent == "BILLING_DISPUTE"
    assert res.confidence >= 0.80


def test_multi_intent_classification():
    text = "My 5G roaming is down in Frankfurt and my invoice charged me twice ($30) for data."
    res = intent_classifier.classify(text)
    assert "BILLING_DISPUTE" in res.all_intents
    assert "NETWORK_OUTAGE_DIAGNOSTICS" in res.all_intents
    assert res.extracted_entities.disputed_amount == 30.0


def test_sentiment_frustration_velocity():
    # Turn 1: Slightly negative
    res1 = sentiment_analyzer.analyze("My bill seems a little higher than usual.")
    assert res1.sentiment_score <= 0.0

    # Turn 2: Plunging into severe frustration
    res2 = sentiment_analyzer.analyze(
        "This is completely unacceptable and horrible service, you are useless!",
        historical_turn_sentiments=[res1.sentiment_score]
    )
    assert res2.sentiment_score <= -0.6
    assert res2.is_sentiment_critical is True
    assert res2.frustration_velocity < 0.0


def test_churn_risk_prediction():
    # Customer with multiple disputes and negative sentiment
    res_high = churn_predictor.predict(
        tenure_months=12,
        recent_disputes_count=2,
        unbilled_usage_usd=50.0,
        billing_status="disputed_unpaid",
        current_sentiment=-0.7,
        is_in_outage_sector=True,
    )
    assert res_high.churn_probability > 0.75
    assert res_high.is_high_churn_risk is True
    assert res_high.risk_level in ["HIGH", "CRITICAL"]

    # Loyal VIP with no disputes
    res_low = churn_predictor.predict(
        tenure_months=60,
        recent_disputes_count=0,
        unbilled_usage_usd=0.0,
        billing_status="current_paid",
        current_sentiment=0.5,
        is_in_outage_sector=False,
    )
    assert res_low.churn_probability < 0.30
    assert res_low.is_high_churn_risk is False
