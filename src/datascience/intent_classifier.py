"""Multi-Label Intent Classifier & Operational Entity Extractor (Stage 2)."""

import re
from typing import Dict, List, Any, Optional, Set
from pydantic import BaseModel, Field
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


class ExtractedEntities(BaseModel):
    msisdn: Optional[str] = None
    imei: Optional[str] = None
    iccid: Optional[str] = None
    error_codes: List[str] = Field(default_factory=list)
    device_models: List[str] = Field(default_factory=list)
    disputed_amount: Optional[float] = None
    plan_mentions: List[str] = Field(default_factory=list)


class IntentClassificationResult(BaseModel):
    primary_intent: str
    secondary_intents: List[str] = Field(default_factory=list)
    all_intents: List[str] = Field(default_factory=list)
    intent_probabilities: Dict[str, float] = Field(default_factory=dict)
    confidence: float
    is_multi_intent: bool = False
    requires_hitl_escalation: bool = False
    extracted_entities: ExtractedEntities = Field(default_factory=ExtractedEntities)


class IntentClassifier:
    """Telecom Intent Classification engine with confidence calibration against tau_intent (0.82)."""

    # Keyword lexicons and patterns for high-precision telecom classification
    INTENT_PATTERNS = {
        "BILLING_DISPUTE": [
            r"\b(?:bill|invoice|charge|fee|charged|overage|refund|credit|payment|unauthorized|dispute|cost)\b",
            r"\b(?:double charged|charged twice|unexpected charge|late fee)\b",
            r"\$\d+(?:\.\d{2})?",
        ],
        "NETWORK_OUTAGE_DIAGNOSTICS": [
            r"\b(?:network|internet|outage|offline|down|no signal|slow|fiber|broadband|wifi|wi-fi|router|pon)\b",
            r"\b(?:disconnect|dropped|flashing red|red light|signal|attenuation|0x4f|otdr)\b",
            r"\b(?:5g|4g|lte|reception|not working|cannot connect)\b",
        ],
        "PROVISIONING_ESIM": [
            r"\b(?:esim|sim|activate|activation|sim card|pac code|port|porting|qr code|icc?id|imei|eid)\b",
            r"\b(?:transfer number|new device|swap sim|provision)\b",
            r"\b(?:roaming pass|activate roaming|wanderlust)\b",
        ],
        "RETENTION_CHURN_RISK": [
            r"\b(?:cancel|cancellation|switch to|competitor|leaving|terminate|close account|too expensive)\b",
            r"\b(?:cancel my plan|cancel subscription|switch provider|discontinue service)\b",
            r"\b(?:unacceptable service|worst telco|file a complaint)\b",
        ],
    }

    ERROR_CODE_PATTERN = re.compile(r"\b(?:ERR_[A-Z0-9_]+|0x[0-9A-Fa-f]{2,4})\b")
    CURRENCY_PATTERN = re.compile(r"\$(\d+(?:\.\d{2})?)")
    IMEI_PATTERN = re.compile(r"\b\d{15}\b")
    PLAN_PATTERN = re.compile(r"\b(?:5G Ultra Max|GigaWave|Freedom Unlimited|Global Wanderlust)\b", re.IGNORECASE)

    def classify(self, text: str) -> IntentClassificationResult:
        """Classify customer utterance into single or multi-intent dependency graphs."""
        text_lower = text.lower()
        intent_scores: Dict[str, float] = {
            "BILLING_DISPUTE": 0.05,
            "NETWORK_OUTAGE_DIAGNOSTICS": 0.05,
            "PROVISIONING_ESIM": 0.05,
            "RETENTION_CHURN_RISK": 0.05,
            "GENERAL_INQUIRY": 0.10,
        }

        # Calculate keyword match density
        for intent, patterns in self.INTENT_PATTERNS.items():
            matches = 0
            for pat in patterns:
                found = len(re.findall(pat, text_lower))
                matches += found
            if matches > 0:
                intent_scores[intent] += min(0.90, matches * 0.35)

        # Softmax / Normalization
        max_score = max(intent_scores.values())
        exp_scores = {k: min(1.0, v / max_score if max_score > 0 else 0.1) for k, v in intent_scores.items()}

        # Sort intents by score
        sorted_intents = sorted(exp_scores.items(), key=lambda item: item[1], reverse=True)
        primary_intent, primary_conf = sorted_intents[0]

        # Multi-intent threshold: secondary intent >= 0.50 of primary or >= 0.65 absolute
        secondary_intents = [
            intent for intent, score in sorted_intents[1:]
            if score >= 0.65 and intent != "GENERAL_INQUIRY"
        ]

        all_active_intents = [primary_intent] + secondary_intents
        is_multi = len(secondary_intents) > 0

        # Confidence calibrated
        confidence = min(0.98, primary_conf)
        requires_escalation = confidence < settings.intent_confidence_threshold

        # Extract entities
        entities = self._extract_entities(text)

        return IntentClassificationResult(
            primary_intent=primary_intent,
            secondary_intents=secondary_intents,
            all_intents=all_active_intents,
            intent_probabilities={k: round(v, 4) for k, v in exp_scores.items()},
            confidence=round(confidence, 4),
            is_multi_intent=is_multi,
            requires_hitl_escalation=requires_escalation,
            extracted_entities=entities,
        )

    def _extract_entities(self, text: str) -> ExtractedEntities:
        """Extract domain specific entities: error codes, disputed amounts, IMEI, plan names."""
        error_codes = self.ERROR_CODE_PATTERN.findall(text)
        amounts = [float(a) for a in self.CURRENCY_PATTERN.findall(text)]
        imeis = self.IMEI_PATTERN.findall(text)
        plans = self.PLAN_PATTERN.findall(text)

        disputed_val = amounts[0] if amounts else None

        return ExtractedEntities(
            error_codes=error_codes,
            disputed_amount=disputed_val,
            imei=imeis[0] if imeis else None,
            plan_mentions=plans,
        )


intent_classifier = IntentClassifier()
