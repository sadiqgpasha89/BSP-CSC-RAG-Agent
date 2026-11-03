"""Performance Benchmarks: Latency Percentiles & Processing Speed."""

import time
from src.agents.supervisor import supervisor_agent
from src.datascience.pii_scrubber import pii_scrubber


def test_pii_masking_throughput_benchmark():
    sample_text = "Call me at (415) 555-0199 or charge my card 4532 1234 5678 8910 for invoice dispute."
    times = []
    for _ in range(50):
        t0 = time.perf_counter()
        pii_scrubber.mask(sample_text)
        times.append((time.perf_counter() - t0) * 1000.0)

    p95 = sorted(times)[int(len(times) * 0.95)]
    assert p95 < 5.0  # P95 well below 5ms


def test_supervisor_turn_latency_benchmark():
    t0 = time.perf_counter()
    res = supervisor_agent.process_turn(
        user_utterance="What is the data allowance on the 5G Ultra Max plan?",
        subscriber_id="SUB-773019",
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # End-to-end latency must remain well under target SLA (< 800ms locally)
    assert elapsed_ms < 800.0
    assert res.latency_ms > 0.0
