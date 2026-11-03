"""Deterministic Policy & Guardrails Layer (Stage 7)."""

import re
from typing import List, Dict, Any, Tuple, Optional
from pydantic import BaseModel, Field
from src.core.logging import get_logger

logger = get_logger(__name__)


class GuardrailVerdict(BaseModel):
    passed: bool
    faithfulness_score: float  # Entailment score between synthesized text and RAG context
    violations: List[str] = Field(default_factory=list)
    sanitized_response: str


class DeterministicGuardrails:
    """Validates synthesized response against factuality, regulatory policies, and CPNI leak prevention."""

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
        clean_text = response_text

        # 1. Check for unmasked PII / CPNI leaks in output
        for pat in self.UNMASKED_PII_PATTERNS:
            if pat.search(response_text):
                violations.append("CPNI Compliance Breach: Raw payment or national ID credential exposed in response output.")

        # 2. Check for unauthorized promises or policy violations
        for pat_str in self.FORBIDDEN_PROMISES:
            if re.search(pat_str, response_text, re.IGNORECASE):
                violations.append(f"Policy Violation: Unauthorized commercial commitment detected ('{pat_str}')")

        # 3. Factuality / Groundedness against RAG Context and Executed Tool Outputs
        faithfulness = self._calculate_faithfulness(response_text, retrieved_contexts, tool_outputs)
        if (retrieved_contexts or tool_outputs) and faithfulness < 0.65:
            violations.append(f"Factuality Hallucination: Synthesized claims lack sufficient semantic entailment in RAG/Tool context (Score: {faithfulness:.2f})")

        passed = len(violations) == 0
        if not passed:
            logger.warning("Guardrail validation failed", violations=violations, faithfulness=faithfulness)

        return GuardrailVerdict(
            passed=passed,
            faithfulness_score=round(faithfulness, 4),
            violations=violations,
            sanitized_response=clean_text,
        )

    def _calculate_faithfulness(
        self,
        response_text: str,
        retrieved_contexts: List[Dict[str, Any]],
        tool_outputs: Optional[List[Dict[str, Any]]] = None,
    ) -> float:
        """Calculate word and entity overlap entailment between draft response, RAG chunks, and tool execution outputs."""
        if not retrieved_contexts and not tool_outputs:
            return 1.0  # Zero hallucination penalty if purely conversational without external grounding

        context_texts = [c.get("content", "") for c in (retrieved_contexts or [])]
        if tool_outputs:
            import json
            for tool in tool_outputs:
                context_texts.append(json.dumps(tool))

        context_corpus = " ".join(context_texts).lower()
        stopwords = {"your", "under", "with", "from", "that", "this", "have", "been", "will", "more", "much", "very"}
        response_words = [w for w in re.findall(r"\b\w{3,}\b", response_text.lower()) if w not in stopwords]
        if not response_words:
            return 1.0

        supported_words = sum(1 for w in response_words if w in context_corpus)
        return min(1.0, (supported_words / len(response_words)) * 1.35)


guardrails_service = DeterministicGuardrails()
