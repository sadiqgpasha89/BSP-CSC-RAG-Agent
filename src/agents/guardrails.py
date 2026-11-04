"""Deterministic Policy & Guardrails Layer (Stage 7)."""

import json
import re
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

from src.core.logging import get_logger

logger = get_logger(__name__)

SAFE_FALLBACK_RESPONSE = (
    "I'm not able to confirm that automatically. I've passed your request, with everything I've gathered so far, "
    "to a specialist who will follow up with you directly."
)

STOPWORDS = set(ENGLISH_STOP_WORDS) | {"your", "under", "with", "from", "that", "this", "have", "been", "will", "more", "much", "very"}
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_\-.])\$?(\d+(?:\.\d+)?)(?:[A-Za-z%]{0,4})(?![A-Za-z0-9_\-])")
_WORD_RE = re.compile(r"[a-z0-9]{4,}")  # informative tokens only; 1-3 letter words carry no factual content
_SUFFIXES = ("ings", "ing", "ed", "es", "s")


def _stem(word: str) -> str:
    """Tiny suffix stripper so 'charge'/'charges' and 'credit'/'credited' count as the same evidence."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[: -len(suffix)]
            break
    return word[:-1] if word.endswith("e") and len(word) > 4 else word


class GuardrailVerdict(BaseModel):
    passed: bool
    faithfulness_score: float  # Entailment score between synthesized text and RAG context
    violations: List[str] = Field(default_factory=list)
    sanitized_response: str


class DeterministicGuardrails:
    """Validates synthesized response against factuality, regulatory policies, and CPNI leak prevention.

    On any violation the draft is *withheld*: ``sanitized_response`` becomes a safe fallback and the supervisor
    escalates. (Previously ``sanitized_response`` was the unmodified draft, so a "blocked" response still reached
    the customer.)
    """

    FORBIDDEN_PROMISES = [
        r"\b(?:waive your entire bill forever|free service for life|unlimited credit with no limit)\b",
        r"\b(?:guaranteed 100% 0ms latency|no outages ever again)\b",
        r"\b(?:bypass security without pin|no verification needed)\b",
    ]

    UNMASKED_PII_PATTERNS = [
        re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),  # Credit Card
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),       # SSN
    ]

    def validate(
        self,
        response_text: str,
        retrieved_contexts: List[Dict[str, Any]],
        tool_outputs: Optional[List[Dict[str, Any]]] = None,
    ) -> GuardrailVerdict:
        """Run multi-layer deterministic validation on draft LLM synthesis."""
        violations: List[str] = []

        # 1. Check for unmasked PII / CPNI leaks in output
        for pat in self.UNMASKED_PII_PATTERNS:
            if pat.search(response_text):
                violations.append("CPNI Compliance Breach: Raw payment or national ID credential exposed in response output.")
                break

        # 2. Check for unauthorized promises or policy violations
        for pat_str in self.FORBIDDEN_PROMISES:
            if re.search(pat_str, response_text, re.IGNORECASE):
                violations.append(f"Policy Violation: Unauthorized commercial commitment detected ('{pat_str}')")

        # 3. Factuality / Groundedness against RAG Context and Executed Tool Outputs
        context_corpus = self._build_corpus(retrieved_contexts, tool_outputs)
        faithfulness = self._calculate_faithfulness(response_text, context_corpus)
        if context_corpus and faithfulness < 0.65:
            violations.append(f"Factuality Hallucination: Synthesized claims lack sufficient semantic entailment in RAG/Tool context (Score: {faithfulness:.2f})")

        # 4. Numeric grounding: every figure quoted to the customer (allowances, fees, credits, distances) must exist
        # in the evidence. This is the deterministic backstop for the legacy-plan trap ("50GB" vs a grandfathered "10GB").
        if context_corpus:
            ungrounded = self._ungrounded_numbers(response_text, context_corpus)
            if ungrounded:
                violations.append(f"Factuality Hallucination: Ungrounded numeric claim(s) not present in evidence: {sorted(ungrounded)}")

        passed = len(violations) == 0
        if not passed:
            logger.warning("Guardrail validation failed", violations=violations, faithfulness=faithfulness)

        return GuardrailVerdict(
            passed=passed,
            faithfulness_score=round(faithfulness, 4),
            violations=violations,
            sanitized_response=response_text if passed else SAFE_FALLBACK_RESPONSE,
        )

    @staticmethod
    def _build_corpus(
        retrieved_contexts: Optional[List[Dict[str, Any]]],
        tool_outputs: Optional[List[Dict[str, Any]]],
    ) -> str:
        texts = [c.get("content", "") for c in (retrieved_contexts or [])]
        for tool in tool_outputs or []:
            texts.append(json.dumps(tool, default=str))
        return " ".join(texts).lower()

    @staticmethod
    def _numbers(text: str) -> Set[float]:
        return {float(m) for m in _NUMBER_RE.findall(text)}

    def _ungrounded_numbers(self, response_text: str, context_corpus: str) -> Set[float]:
        return self._numbers(response_text) - self._numbers(context_corpus)

    def _calculate_faithfulness(self, response_text: str, context_corpus: str) -> float:
        """Whole-word overlap between draft response and evidence (rescaled; a cheap NLI stand-in).

        Matching is on word *sets*; the earlier substring test counted "net" as supported by "network".
        """
        if not context_corpus:
            return 1.0  # purely conversational, nothing to ground against

        response_words = [_stem(w) for w in _WORD_RE.findall(response_text.lower()) if w not in STOPWORDS]
        if not response_words:
            return 1.0

        context_words = {_stem(w) for w in _WORD_RE.findall(context_corpus)}
        supported = sum(1 for w in response_words if w in context_words)
        return min(1.0, (supported / len(response_words)) * 1.35)


guardrails_service = DeterministicGuardrails()
