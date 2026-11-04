"""Sub-millisecond Ingress PII & CPNI Masking Enclave (Stage 1)."""

import re
import time
from typing import Dict, List, Tuple

from pydantic import BaseModel, Field

from src.core.logging import get_logger

logger = get_logger(__name__)


class PIIScrubResult(BaseModel):
    # NOTE: ``original_text`` and ``token_map`` hold raw PII. They must stay inside the enclave: never log,
    # persist or serialise this model to a client; only ``masked_text`` may leave.
    original_text: str
    masked_text: str
    token_map: Dict[str, str] = Field(default_factory=dict)
    redacted_entities_count: int = 0
    processing_time_ms: float = 0.0


class PIIScrubber:
    """High-throughput regex and heuristic PII/CPNI sanitizer for telecommunications ingress streams."""

    # Pre-compiled high-performance patterns. Order matters: most specific / longest numeric identifiers first so a
    # 16-digit card is never partially consumed by the 15-digit IMEI rule.
    PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
        ("CREDIT_CARD", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")),
        ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
        ("ICCID", re.compile(r"\b89\d{16,18}\b")),
        ("IMEI", re.compile(r"\b\d{15}\b")),
        # Lookarounds instead of \b: "\b" never matches before "(" or "+", so "(415) 555-0199" and "+1-415-..."
        # were previously missed / only partially masked.
        ("MSISDN", re.compile(r"(?<![\w<])(?:\+?1[-. ]?)?(?:\([2-9]\d{2}\)|[2-9]\d{2})[-. ]?[2-9]\d{2}[-. ]?\d{4}(?![\w>])")),
    ]
    PIN_PATTERN = re.compile(
        r"(?i)\b(?:pin|password|passcode|secret)(?:\s+(?:pin|code|number|is|key))*[\s:=]+([A-Za-z0-9@#$%^&*]{4,16})\b"
    )

    def mask(self, text: str) -> PIIScrubResult:
        """Sanitize text by replacing sensitive PII/CPNI entities with deterministic pseudonyms."""
        start_time = time.perf_counter()
        token_map: Dict[str, str] = {}
        counter = 0
        masked_text = text

        for label, pattern in self.PATTERNS:
            def _sub(match: "re.Match[str]", label: str = label) -> str:
                nonlocal counter
                counter += 1
                token = f"<{label}_{counter}>"
                token_map[token] = match.group(0)
                return token

            # Single-pass substitution (rather than str.replace of the matched value, which also rewrote
            # unrelated occurrences of the same digits and could corrupt previously inserted tokens).
            masked_text = pattern.sub(_sub, masked_text)

        def _pin_sub(match: "re.Match[str]") -> str:
            nonlocal counter
            counter += 1
            token = "<PIN_REDACTED>" if "<PIN_REDACTED>" not in token_map else f"<PIN_REDACTED_{counter}>"
            token_map[token] = match.group(1)
            return match.group(0).replace(match.group(1), token)

        masked_text = self.PIN_PATTERN.sub(_pin_sub, masked_text)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return PIIScrubResult(
            original_text=text,
            masked_text=masked_text,
            token_map=token_map,
            redacted_entities_count=len(token_map),
            processing_time_ms=round(elapsed_ms, 3),
        )

    def unmask(self, masked_text: str, token_map: Dict[str, str]) -> str:
        """Restore masked tokens back to their original values when safe or in internal enclave."""
        restored = masked_text
        for token, original_val in token_map.items():
            restored = restored.replace(token, original_val)
        return restored


pii_scrubber = PIIScrubber()
