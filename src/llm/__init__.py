"""LLM provider interface.

The language model is only ever allowed to *phrase* a reply that the deterministic layers already decided on. It never
chooses tools, amounts, recipients or permissions (those live in ``src/tools/gateway.py`` and ``config/policies.json``).
``grounded_rephrase`` enforces that: any model output that adds a number, identifier or URL absent from the facts it was
given is discarded and the deterministic draft is used instead.

Default provider is ``mock`` (returns the draft unchanged). ``OpenAICompatibleProvider`` is a thin adapter for a
self-hosted vLLM/OpenAI-style endpoint; it is NOT enabled by default and has not been exercised against a live model here.
"""

import re
from abc import ABC, abstractmethod
from typing import Optional

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

_FACT_TOKEN = re.compile(r"[A-Z]{2,}[-_][A-Za-z0-9\-_]+|https?://\S+|\$?\d[\d,.:/-]*")


class LLMProvider(ABC):
    name = "abstract"

    @abstractmethod
    def rephrase(self, draft: str, language: str, tone: str = "friendly, concise") -> str:
        """Return the same content as ``draft`` in a more natural voice and the requested language."""


class MockProvider(LLMProvider):
    name = "mock"

    def rephrase(self, draft: str, language: str, tone: str = "friendly, concise") -> str:
        return draft


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(self, endpoint: str, model: str, api_key: Optional[str] = None, timeout: float = 8.0):
        self.endpoint, self.model, self.api_key, self.timeout = endpoint.rstrip("/"), model, api_key, timeout

    def rephrase(self, draft: str, language: str, tone: str = "friendly, concise") -> str:
        import httpx  # lazy: only needed when this provider is selected
        system = ("Rewrite the customer-care reply. Keep every number, ID, date and amount exactly as given. Do not add facts, "
                  f"promises or links. Language: {language}. Tone: {tone}. Output only the reply.")
        r = httpx.post(f"{self.endpoint}/chat/completions", timeout=self.timeout,
                       headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                       json={"model": self.model, "temperature": 0.2, "messages": [{"role": "system", "content": system}, {"role": "user", "content": draft}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


def get_provider() -> LLMProvider:
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider(settings.llm_endpoint, settings.llm_model, settings.llm_api_key)
    return MockProvider()


def grounded_rephrase(draft: str, language: str, provider: Optional[LLMProvider] = None) -> str:
    """Use the provider's wording only if it introduces no new numbers/identifiers/links; otherwise keep the draft. Never raises."""
    p = provider or get_provider()
    try:
        out = p.rephrase(draft, language)
    except Exception as exc:  # provider down, timeout, bad payload: the deterministic reply is always a valid answer
        logger.warning("LLM provider failed; using deterministic draft", provider=p.name, error=repr(exc))
        return draft
    if not out or not out.strip():
        return draft
    def tokens(text):
        return {t.rstrip(".,:;)/-") for t in _FACT_TOKEN.findall(text)}

    added = sorted(tokens(out) - tokens(draft))
    if added:
        logger.warning("LLM output introduced ungrounded facts; discarded", provider=p.name, added=added[:5])
        return draft
    return out
