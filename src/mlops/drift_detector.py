"""Multi-Dimensional Drift Detection Engine (Section 9)."""

import math
import numpy as np
from scipy import stats
from typing import Dict, List, Any, Tuple, Optional
from pydantic import BaseModel, Field
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


class DriftReport(BaseModel):
    data_drift_detected: bool
    psi_score: float
    ks_statistic: float
    ks_p_value: float
    concept_drift_detected: bool
    semantic_centroid_drift: float
    tool_drift_detected: bool
    tool_frequency_anomaly: Dict[str, float]
    rag_drift_detected: bool
    current_rag_similarity_ratio: float
    mitigation_triggered: bool
    recommended_actions: List[str] = Field(default_factory=list)
    input_source: str = "caller_supplied"  # provenance of the analysed window (live telemetry vs synthetic sample)


class MultiDimensionalDriftDetector:
    """Monitors Data Drift, Concept Drift, Agentic Tool Drift, and RAG Semantic Drift."""

    def __init__(self):
        # Baseline reference distributions (e.g. historical 30-day baseline)
        self.baseline_utterance_lengths = np.array([12, 18, 25, 30, 15, 22, 28, 35, 14, 20, 26, 32, 16, 24, 38])
        self.baseline_centroid = np.array([0.25, 0.45, 0.15, 0.65, 0.30, 0.50])
        self.baseline_tool_frequencies = {
            "bss_adapter": 0.45,
            "oss_adapter": 0.35,
            "provisioning_adapter": 0.20,
        }
        self.baseline_rag_mean_similarity = 0.88

    def calculate_psi(self, baseline: np.ndarray, target: np.ndarray, num_bins: int = 5) -> float:
        """Compute Population Stability Index: PSI = sum( (P_i - Q_i) * ln(P_i / Q_i) )."""
        if len(target) == 0 or len(baseline) == 0:
            return 0.0

        min_val = min(baseline.min(), target.min())
        max_val = max(baseline.max(), target.max()) + 1e-5
        bins = np.linspace(min_val, max_val, num_bins + 1)

        b_counts, _ = np.histogram(baseline, bins=bins)
        t_counts, _ = np.histogram(target, bins=bins)

        # Normalize to probabilities with Laplace smoothing
        b_probs = (b_counts + 0.001) / (len(baseline) + 0.001 * num_bins)
        t_probs = (t_counts + 0.001) / (len(target) + 0.001 * num_bins)

        psi = np.sum((t_probs - b_probs) * np.log(t_probs / b_probs))
        return float(max(0.0, psi))

    def detect_drift(
        self,
        recent_utterance_lengths: List[int],
        recent_embeddings_sample: Optional[np.ndarray] = None,
        recent_tool_counts: Optional[Dict[str, int]] = None,
        recent_rag_similarities: Optional[List[float]] = None,
    ) -> DriftReport:
        """Run comprehensive multi-dimensional statistical drift inspection."""
        actions: List[str] = []

        # 1. Data Drift (PSI and Kolmogorov-Smirnov test on input token/char length)
        target_lengths = np.array(recent_utterance_lengths if recent_utterance_lengths else [15, 20, 25])
        psi = self.calculate_psi(self.baseline_utterance_lengths, target_lengths)
        ks_res = stats.ks_2samp(self.baseline_utterance_lengths, target_lengths)
        data_drift = (psi >= settings.drift_psi_threshold) or (ks_res.pvalue < settings.drift_ks_p_value_threshold)

        if data_drift:
            actions.append(f"Input distribution shift detected (PSI: {psi:.3f}). Retune ASR and text tokenizer.")

        # 2. Concept Drift (Semantic Centroid shift Delta_drift)
        if recent_embeddings_sample is not None and len(recent_embeddings_sample) > 0:
            target_centroid = np.mean(recent_embeddings_sample, axis=0)
            norm_b = np.linalg.norm(self.baseline_centroid)
            norm_t = np.linalg.norm(target_centroid)
            cos_sim = np.dot(self.baseline_centroid, target_centroid) / (norm_b * norm_t + 1e-9)
            centroid_drift = max(0.0, 1.0 - cos_sim)
        else:
            centroid_drift = 0.05

        concept_drift = centroid_drift > 0.25
        if concept_drift:
            actions.append(f"Emergent concept shift observed (Delta_drift: {centroid_drift:.3f}). Trigger intent model re-training.")

        # 3. Agentic & Tool Drift (Spikes in specific tool invocations)
        tool_anomalies: Dict[str, float] = {}
        tool_drift = False
        if recent_tool_counts:
            total_calls = max(1, sum(recent_tool_counts.values()))
            for tool, count in recent_tool_counts.items():
                observed_freq = count / total_calls
                expected_freq = self.baseline_tool_frequencies.get(tool, 0.33)
                ratio = observed_freq / max(0.01, expected_freq)
                tool_anomalies[tool] = round(ratio, 2)
                if ratio >= 2.5:  # Spike of 2.5x or higher
                    tool_drift = True
                    actions.append(f"Tool invocation surge for '{tool}' ({ratio:.1f}x baseline). Check upstream OSS/BSS incidents.")

        # 4. RAG Semantic Drift (Corpus-Query Alignment degradation)
        if recent_rag_similarities:
            current_mean_sim = float(np.mean(recent_rag_similarities))
        else:
            current_mean_sim = 0.85

        sim_ratio = current_mean_sim / self.baseline_rag_mean_similarity
        rag_drift = sim_ratio < settings.rag_similarity_drift_threshold

        if rag_drift:
            actions.append(f"RAG retrieval similarity dropped below 85% baseline ({sim_ratio*100:.1f}%). Trigger knowledge base re-indexing.")

        mitigation_needed = data_drift or concept_drift or tool_drift or rag_drift

        return DriftReport(
            data_drift_detected=data_drift,
            psi_score=round(psi, 4),
            ks_statistic=round(float(ks_res.statistic), 4),
            ks_p_value=round(float(ks_res.pvalue), 4),
            concept_drift_detected=concept_drift,
            semantic_centroid_drift=round(centroid_drift, 4),
            tool_drift_detected=tool_drift,
            tool_frequency_anomaly=tool_anomalies,
            rag_drift_detected=rag_drift,
            current_rag_similarity_ratio=round(sim_ratio, 4),
            mitigation_triggered=mitigation_needed,
            recommended_actions=actions,
        )


drift_detector = MultiDimensionalDriftDetector()
