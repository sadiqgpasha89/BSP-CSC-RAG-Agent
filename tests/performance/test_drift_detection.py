"""Performance Benchmarks: Drift Detection Speed & Stability."""

import time
import numpy as np
from src.mlops.drift_detector import drift_detector


def test_drift_computation_performance():
    sample_lengths = [int(x) for x in np.random.normal(25, 5, 200)]
    sample_tools = {"bss_adapter": 50, "oss_adapter": 40, "provisioning_adapter": 25}

    t0 = time.perf_counter()
    report = drift_detector.detect_drift(
        recent_utterance_lengths=sample_lengths,
        recent_tool_counts=sample_tools,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Must execute in < 25ms
    assert elapsed_ms < 25.0
    assert report.psi_score >= 0.0
