from src.llm import LLMProvider, MockProvider, grounded_rephrase

DRAFT = "Your balance is $2.40, valid until 2026-12-15. Reference TCK-AB12CD34."


class Fake(LLMProvider):
    name = "fake"

    def __init__(self, out=None, exc=None):
        self.out, self.exc = out, exc

    def rephrase(self, draft, language, tone=""):
        if self.exc:
            raise self.exc
        return self.out


def test_mock_provider_is_identity():
    assert grounded_rephrase(DRAFT, "en", MockProvider()) == DRAFT


def test_rewording_that_keeps_facts_is_accepted():
    out = "Good news: you have $2.40 left until 2026-12-15 (ref TCK-AB12CD34)."
    assert grounded_rephrase(DRAFT, "en", Fake(out)) == out


def test_invented_amount_or_link_is_discarded():
    assert grounded_rephrase(DRAFT, "en", Fake("You have $240.00 left.")) == DRAFT
    assert grounded_rephrase(DRAFT, "en", Fake(DRAFT + " Visit https://evil.example")) == DRAFT


def test_provider_failure_or_empty_output_falls_back():
    assert grounded_rephrase(DRAFT, "en", Fake(exc=TimeoutError())) == DRAFT
    assert grounded_rephrase(DRAFT, "en", Fake("  ")) == DRAFT
