"""Telecom Acoustic ASR Transcription & Dialect Normalization (Stage 1)."""

import re
from typing import Dict, List, Tuple
from pydantic import BaseModel


class ASRTranscriptionResult(BaseModel):
    raw_transcript: str
    normalized_transcript: str
    word_error_rate: float
    detected_dialect_or_code_switching: bool
    substituted_entities: Dict[str, str]


class ASRTranscriber:
    """Simulates voice streaming ASR ingestion with telecom domain acoustic adaptation and code-switching normalization."""

    # Colloquial / Multilingual dialect normalization dictionary
    CODE_SWITCH_MAPPINGS = {
        "mera internet pack": "my data allowance",
        "net pack": "data allowance",
        "recharge": "bill payment",
        "balance check karna": "check balance",
        "activate nahi ho rahi": "fails to activate",
        "chal nahi raha": "not working",
        "mi plan": "my plan",
        "no funciona": "not working",
        "recarga": "top up",
        "sin servicio": "no service",
    }

    def transcribe(self, simulated_audio_text: str, ground_truth: str = None) -> ASRTranscriptionResult:
        """Normalize colloquial dialect code-switching and calculate WER if ground truth provided."""
        text = simulated_audio_text.strip()
        normalized = text.lower()
        has_code_switching = False
        substitutions = {}

        for pattern, replacement in self.CODE_SWITCH_MAPPINGS.items():
            if pattern in normalized:
                has_code_switching = True
                normalized = normalized.replace(pattern, replacement)
                substitutions[pattern] = replacement

        wer = self.calculate_wer(reference=ground_truth or text, hypothesis=text)

        return ASRTranscriptionResult(
            raw_transcript=text,
            normalized_transcript=normalized,
            word_error_rate=wer,
            detected_dialect_or_code_switching=has_code_switching,
            substituted_entities=substitutions,
        )

    def calculate_wer(self, reference: str, hypothesis: str) -> float:
        """Compute exact Word Error Rate: WER = (Substitutions + Deletions + Insertions) / N."""
        r_words = re.findall(r"\b\w+\b", reference.lower())
        h_words = re.findall(r"\b\w+\b", hypothesis.lower())

        if not r_words:
            return 0.0 if not h_words else 1.0

        # Dynamic programming Levenshtein distance on words
        d = [[0] * (len(h_words) + 1) for _ in range(len(r_words) + 1)]
        for i in range(len(r_words) + 1):
            d[i][0] = i
        for j in range(len(h_words) + 1):
            d[0][j] = j

        for i in range(1, len(r_words) + 1):
            for j in range(1, len(h_words) + 1):
                if r_words[i - 1] == h_words[j - 1]:
                    d[i][j] = d[i - 1][j - 1]
                else:
                    substitution = d[i - 1][j - 1] + 1
                    insertion = d[i][j - 1] + 1
                    deletion = d[i - 1][j] + 1
                    d[i][j] = min(substitution, insertion, deletion)

        return round(d[len(r_words)][len(h_words)] / len(r_words), 4)


asr_transcriber = ASRTranscriber()
