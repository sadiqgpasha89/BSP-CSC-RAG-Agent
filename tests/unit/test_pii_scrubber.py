"""Unit Tests for Sub-millisecond PII/CPNI Masking Enclave."""

import time
from src.datascience.pii_scrubber import pii_scrubber


def test_pii_masking_credit_card():
    text = "Please charge my Visa 4532 1234 5678 8910 for the $50 bill."
    res = pii_scrubber.mask(text)
    assert "<CREDIT_CARD_1>" in res.masked_text
    assert "4532 1234 5678 8910" not in res.masked_text
    assert res.redacted_entities_count == 1

    # Test unmasking
    restored = pii_scrubber.unmask(res.masked_text, res.token_map)
    assert restored == text


def test_pii_masking_ssn_and_imei():
    text = "My SSN is 123-45-6789 and my phone IMEI is 354891092837192."
    res = pii_scrubber.mask(text)
    assert "<SSN_1>" in res.masked_text
    assert "<IMEI_2>" in res.masked_text or "<IMEI_1>" in res.masked_text
    assert "123-45-6789" not in res.masked_text
    assert "354891092837192" not in res.masked_text


def test_pii_masking_latency():
    text = "Hello, my phone is (415) 555-0199 and my secret PIN is: 8492."
    res = pii_scrubber.mask(text)
    assert res.processing_time_ms < 5.0  # Must be sub-millisecond to low ms
    assert "<PIN_REDACTED>" in res.masked_text
