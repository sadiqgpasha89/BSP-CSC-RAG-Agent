"""Multi-Label Intent Classifier & Operational Entity Extractor (Stage 2)."""

import math
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

    # Softmax temperature mapping evidence strength to probability: one strong lexical hit ~0.88, two hits ~0.99.
    SOFTMAX_TEMPERATURE = 0.10
    # GENERAL_INQUIRY is the catch-all class selected by the *absence* of domain evidence. Its confidence is a fixed
    # prior; the retrieval-relevance gate (tau_rag) is what protects the customer on that path.
    GENERAL_FALLBACK_CONFIDENCE = 0.85
    DOMAIN_INTENTS = ("BILLING_DISPUTE", "NETWORK_OUTAGE_DIAGNOSTICS", "PROVISIONING_ESIM", "RETENTION_CHURN_RISK")

    def classify(self, text: str) -> IntentClassificationResult:
        """Classify customer utterance into single or multi-intent dependency graphs.

        Confidence is the posterior probability mass a temperature-scaled softmax assigns to the *activated* intents.
        The previous implementation divided every score by the maximum score, so the winner always scored 1.0
        (confidence pinned at 0.98) and the tau_intent escalation could never fire.
        """
        text_lower = text.lower()
        raw_scores: Dict[str, float] = {
            "BILLING_DISPUTE": 0.05,
            "NETWORK_OUTAGE_DIAGNOSTICS": 0.05,
            "PROVISIONING_ESIM": 0.05,
            "RETENTION_CHURN_RISK": 0.05,
            "GENERAL_INQUIRY": 0.10,
        }

        # Keyword match density per intent
        has_domain_evidence = False
        for intent, patterns in self.INTENT_PATTERNS.items():
            matches = sum(len(re.findall(pat, text_lower)) for pat in patterns)
            if matches > 0:
                has_domain_evidence = True
                raw_scores[intent] += min(0.90, matches * 0.35)

        # Calibrated posterior over all classes
        exp_scores = {k: math.exp(v / self.SOFTMAX_TEMPERATURE) for k, v in raw_scores.items()}
        z = sum(exp_scores.values())
        probs = {k: v / z for k, v in exp_scores.items()}

        if has_domain_evidence:
            ranked = sorted(self.DOMAIN_INTENTS, key=lambda i: raw_scores[i], reverse=True)
            primary_intent = ranked[0]
            # Multi-intent: a runner-up counts when its evidence is >= 65% of the primary's
            secondary_intents = [i for i in ranked[1:] if raw_scores[i] / raw_scores[primary_intent] >= 0.65]
            active = [primary_intent] + secondary_intents
            confidence = min(0.98, sum(probs[i] for i in active))
        else:
            primary_intent = "GENERAL_INQUIRY"
            secondary_intents = []
            active = [primary_intent]
            confidence = self.GENERAL_FALLBACK_CONFIDENCE

        requires_escalation = confidence < settings.intent_confidence_threshold
        entities = self._extract_entities(text)

        return IntentClassificationResult(
            primary_intent=primary_intent,
            secondary_intents=secondary_intents,
            all_intents=active,
            intent_probabilities={k: round(v, 4) for k, v in probs.items()},
            confidence=round(confidence, 4),
            is_multi_intent=len(secondary_intents) > 0,
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
