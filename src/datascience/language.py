"""Lightweight language identification for the configured operator languages (en, es, hi by default).

Script ranges identify non-Latin text; small marker lexicons separate Spanish and romanised Hindi (Hinglish) from
English. Short or ambiguous messages ("yes", "ok", "500") return low confidence so the conversation keeps its current
language instead of flapping. Production swaps this for a trained identifier behind the same interface.
"""

import re
from typing import Dict, List, Optional

from pydantic import BaseModel

from src.core.config import settings

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_ARABIC = re.compile(r"[؀-ۿ]")
_WORD = re.compile(r"[a-záéíóúñü]+", re.IGNORECASE)

MARKERS: Dict[str, Dict[str, float]] = {
    "es": {w: 1.0 for w in (
        "hola", "gracias", "factura", "cuánto", "cuanto", "mi", "pagar", "quiero", "saldo", "por", "favor", "necesito", "tengo", "puedo",
        "cargo", "cobro", "no", "funciona", "internet", "recarga", "recargar", "plan", "ayuda", "cuenta", "línea", "linea", "cambiar", "queja",
        "buenos", "días", "dias", "tardes", "sí", "si", "cuál", "cual", "está", "esta", "mis", "datos", "sin", "servicio", "el", "la", "los", "una", "un", "de", "que", "qué")},
    "hi": {w: 1.0 for w in (
        "mera", "meri", "mujhe", "nahi", "nahin", "kya", "hai", "hain", "kaise", "kitna", "kitne", "paisa", "chahiye", "karo", "kar", "karna",
        "ho", "raha", "rahi", "bill", "balance", "recharge", "plan", "aap", "main", "mein", "se", "ka", "ki", "ko", "abhi", "kripya", "dhanyavaad",
        "namaste", "shikayat", "pack", "net", "internet", "chal", "kharab", "problem", "jaldi", "madad", "haan", "theek")},
}
STRONG = {"es": {"hola", "gracias", "factura", "cuánto", "cuanto", "quiero", "necesito", "saldo", "queja"},
          "hi": {"mera", "meri", "mujhe", "nahi", "kya", "kaise", "kitna", "chahiye", "namaste", "shikayat", "kripya", "paisa"}}
EN_COMMON = {"the", "my", "is", "i", "to", "what", "how", "can", "you", "please", "bill", "plan", "data", "not", "working", "want", "need", "and", "of", "a", "with", "have", "me", "do", "it"}


class LanguageResult(BaseModel):
    language: str
    confidence: float
    supported: bool
    script: str = "latin"


class LanguageDetector:
    def detect(self, text: str, hint: Optional[str] = None) -> LanguageResult:
        supported = settings.languages
        if _DEVANAGARI.search(text):
            return LanguageResult(language="hi", confidence=0.95, supported="hi" in supported, script="devanagari")
        if _ARABIC.search(text):
            return LanguageResult(language="ar", confidence=0.9, supported="ar" in supported, script="arabic")
        words = [w.lower() for w in _WORD.findall(text)]
        if not words:
            return LanguageResult(language=hint or settings.default_language, confidence=0.0, supported=True)
        scores: Dict[str, float] = {}
        for lang, lex in MARKERS.items():
            hits = [w for w in words if w in lex]
            strong = [w for w in hits if w in STRONG[lang]]
            scores[lang] = len(hits) + 2.0 * len(strong)
        en = sum(1 for w in words if w in EN_COMMON)
        scores["en"] = en * 1.5
        best = max(scores, key=lambda k: scores[k])
        total = sum(scores.values()) or 1.0
        conf = scores[best] / total
        # Too little evidence (e.g. "ok", "yes", "1234"): keep the conversation's current language.
        if scores[best] < 2.0 or len(words) < 2 and scores[best] < 3.0:
            return LanguageResult(language=hint or settings.default_language, confidence=0.0, supported=True)
        return LanguageResult(language=best, confidence=round(min(0.99, conf), 2), supported=best in supported)


language_detector = LanguageDetector()
