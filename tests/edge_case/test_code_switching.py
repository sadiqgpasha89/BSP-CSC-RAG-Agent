"""Edge Case Test: Multilingual Code-Switching & Dialect Normalization (Section 10.5)."""

from src.datascience.asr_transcriber import asr_transcriber
from src.datascience.intent_classifier import intent_classifier


def test_hinglish_code_switching_normalization():
    utterance = "Mera internet pack balance check karna hai and roaming service activate nahi ho rahi."
    res = asr_transcriber.transcribe(utterance)

    assert res.detected_dialect_or_code_switching is True
    assert "data allowance" in res.normalized_transcript
    assert "fails to activate" in res.normalized_transcript

    # Classified properly after normalization
    intent_res = intent_classifier.classify(res.normalized_transcript)
    assert intent_res.primary_intent in ["PROVISIONING_ESIM", "BILLING_DISPUTE", "NETWORK_OUTAGE_DIAGNOSTICS"]


def test_wer_acoustic_calculation():
    reference = "my mobile internet is not working"
    hypothesis = "my mobile internet not working"
    wer = asr_transcriber.calculate_wer(reference, hypothesis)
    assert 0.0 < wer <= 0.25
