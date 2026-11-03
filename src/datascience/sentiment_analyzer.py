"""Sentiment Triage & Frustration Velocity Engine (Stage 2)."""

import re
from typing import List, Dict, Any, Tuple
from pydantic import BaseModel, Field


class SentimentResult(BaseModel):
    sentiment_score: float  # Range: -1.0 (extremely negative) to +1.0 (extremely positive)
    sentiment_label: str    # "POSITIVE", "NEUTRAL", "NEGATIVE", "SEVERELY_FRUSTRATED"
    frustration_velocity: float  # Rate of sentiment change across turns
    is_sentiment_critical: bool  # Flag for severe frustration or degradation


class SentimentAnalyzer:
    """Computes instant sentiment polarity and multi-turn frustration velocity."""

    NEGATIVE_LEXICON = {
        "terrible": 0.8, "horrible": 0.9, "worst": 0.9, "unacceptable": 0.85,
        "furious": 0.95, "angry": 0.8, "useless": 0.75, "ridiculous": 0.7,
        "scam": 0.9, "ripoff": 0.85, "broken": 0.6, "waste": 0.75,
        "sucks": 0.8, "incompetent": 0.85, "lies": 0.85, "cancel": 0.5,
    }

    POSITIVE_LEXICON = {
        "thank": 0.6, "thanks": 0.6, "great": 0.7, "awesome": 0.8,
        "perfect": 0.85, "excellent": 0.9, "appreciate": 0.7, "good": 0.5,
        "resolved": 0.75, "helpful": 0.7, "fast": 0.6, "wonderful": 0.85,
    }

    def analyze(self, text: str, historical_turn_sentiments: List[float] = None) -> SentimentResult:
        """Compute single utterance sentiment and multi-turn velocity."""
        words = re.findall(r"\b\w+\b", text.lower())
        if not words:
            score = 0.0
        else:
            neg_score = sum(self.NEGATIVE_LEXICON.get(w, 0.0) for w in words)
            pos_score = sum(self.POSITIVE_LEXICON.get(w, 0.0) for w in words)
            raw = (pos_score - neg_score) / (len(words) ** 0.5 + 1.0)
            score = max(-1.0, min(1.0, raw * 2.0))

        # Classify label
        if score <= -0.6:
            label = "SEVERELY_FRUSTRATED"
        elif score < -0.15:
            label = "NEGATIVE"
        elif score > 0.25:
            label = "POSITIVE"
        else:
            label = "NEUTRAL"

        # Calculate frustration velocity: d(sentiment) / dt
        history = list(historical_turn_sentiments or [])
        if history:
            prev_sentiment = history[-1]
            velocity = round(score - prev_sentiment, 4)
        else:
            velocity = 0.0

        # Critical threshold if score <= -0.6 or velocity <= -0.45 (rapid drop)
        is_critical = (score <= -0.6) or (velocity <= -0.45)

        return SentimentResult(
            sentiment_score=round(score, 4),
            sentiment_label=label,
            frustration_velocity=velocity,
            is_sentiment_critical=is_critical,
        )


sentiment_analyzer = SentimentAnalyzer()
