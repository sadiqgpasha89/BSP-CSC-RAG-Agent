"""Sub-millisecond Ingress PII & CPNI Masking Enclave (Stage 1)."""

import re
import time
from typing import Dict, Tuple, List
from pydantic import BaseModel, Field
from src.core.logging import get_logger

logger = get_logger(__name__)


class PIIScrubResult(BaseModel):
    original_text: str
    masked_text: str
    token_map: Dict[str, str] = Field(default_factory=dict)
    redacted_entities_count: int = 0
    processing_time_ms: float = 0.0


class PIIScrubber:
    """High-throughput regex and heuristic PII/CPNI sanitizer for telecommunications ingress streams."""

    # Pre-compiled high-performance patterns
    PATTERNS = {
        "CREDIT_CARD": re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),
        "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "MSISDN": re.compile(r"\b(?:\+?1[-. ]?)?(?:\([2-9]\d{2}\)|[2-9]\d{2})[-. ]?[2-9]\d{2}[-. ]?\d{4}\b"),
        "IMEI": re.compile(r"\b\d{15}\b"),
        "ICCID": re.compile(r"\b89\d{16,18}\b"),
        "PASSWORD_PIN": re.compile(r"(?i)\b(?:pin|password|passcode|secret)(?:\s+(?:pin|code|number|is|key))*[\s:=]+([A-Za-z0-9@#$%^&*]{4,16})\b"),
    }

    def mask(self, text: str) -> PIIScrubResult:
        """Sanitize text by replacing sensitive PII/CPNI entities with deterministic pseudonyms."""
        start_time = time.perf_counter()
        token_map: Dict[str, str] = {}
        masked_text = text
        counter = 1

        # 1. Credit Cards
        for match in self.PATTERNS["CREDIT_CARD"].finditer(text):
            val = match.group(0)
            token = f"<CREDIT_CARD_{counter}>"
            masked_text = masked_text.replace(val, token)
            token_map[token] = val
            counter += 1

        # 2. SSN / National IDs
        for match in self.PATTERNS["SSN"].finditer(masked_text):
            val = match.group(0)
            token = f"<SSN_{counter}>"
            masked_text = masked_text.replace(val, token)
            token_map[token] = val
            counter += 1

        # 3. ICCID
        for match in self.PATTERNS["ICCID"].finditer(masked_text):
            val = match.group(0)
            token = f"<ICCID_{counter}>"
            masked_text = masked_text.replace(val, token)
            token_map[token] = val
            counter += 1

        # 4. IMEI
        for match in self.PATTERNS["IMEI"].finditer(masked_text):
            val = match.group(0)
            token = f"<IMEI_{counter}>"
            masked_text = masked_text.replace(val, token)
            token_map[token] = val
            counter += 1

        # 5. MSISDN (Phone numbers)
        for match in self.PATTERNS["MSISDN"].finditer(masked_text):
            val = match.group(0)
            if not val.startswith("<"):
                token = f"<MSISDN_{counter}>"
                masked_text = masked_text.replace(val, token)
                token_map[token] = val
                counter += 1

        # 6. Password / PIN
        for match in self.PATTERNS["PASSWORD_PIN"].finditer(masked_text):
            val = match.group(1)
            token = f"<PIN_REDACTED>"
            masked_text = masked_text.replace(val, token)
            token_map[token] = val

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
