"""Enterprise Circuit Breaker & Resilient Tool Quarantine Pattern."""

import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel

from src.core.logging import get_logger

logger = get_logger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"          # Normal operation
    OPEN = "OPEN"              # Tripped, quarantine active, fail fast to cached fallback
    HALF_OPEN = "HALF_OPEN"    # Testing if service recovered


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the breaker is quarantining the downstream system."""


class CircuitBreakerConfig(BaseModel):
    failure_threshold: int = 3
    recovery_timeout_sec: float = 30.0
    timeout_threshold_ms: float = 5000.0


class CircuitBreaker:
    """Protects agent tool execution from legacy OSS/BSS hangs and cascading failures.

    State transitions are guarded by a lock (requests are served from a thread pool) and HALF_OPEN admits exactly
    one probe call; concurrent callers keep failing fast until the probe reports back. Note that
    ``timeout_threshold_ms`` is a *post-hoc* SLA check (a slow success still counts as a failure); hard
    cancellation of an in-flight call has to be enforced by the transport client's own timeout.
    """

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.total_calls = 0
        self.tripped_count = 0
        self._probe_in_flight = False
        self._lock = threading.RLock()

    def _admit(self) -> bool:
        """Decide whether a call may proceed; returns True if it is the HALF_OPEN probe."""
        with self._lock:
            self.total_calls += 1

            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time > self.config.recovery_timeout_sec:
                    logger.info("Circuit transitioning from OPEN to HALF_OPEN", circuit=self.name)
                    self.state = CircuitState.HALF_OPEN
                else:
                    raise CircuitOpenError(f"Circuit Breaker '{self.name}' is OPEN. Tool quarantined.")

            if self.state == CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    raise CircuitOpenError(f"Circuit Breaker '{self.name}' is HALF_OPEN; recovery probe in flight.")
                self._probe_in_flight = True
                return True
            return False

    def call(self, func: Callable[..., Any], *args, fallback: Optional[Callable[..., Any]] = None, **kwargs) -> Any:
        """Execute protected function with SLA monitoring and quarantine isolation."""
        try:
            is_probe = self._admit()
        except CircuitOpenError:
            logger.warning("Circuit rejected call", circuit=self.name)
            if fallback:
                return fallback(*args, **kwargs)
            raise

        start_time = time.perf_counter()
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            logger.error("Protected tool call raised exception", circuit=self.name, error=str(e))
            self._record_failure(is_probe)
            if fallback:
                return fallback(*args, **kwargs)
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        if duration_ms > self.config.timeout_threshold_ms:
            logger.warning("Tool execution latency breached SLA threshold", circuit=self.name, latency_ms=duration_ms)
            self._record_failure(is_probe)
        else:
            self._record_success(is_probe)
        return result

    def _record_success(self, was_probe: bool = False) -> None:
        """Reset failure counts upon successful healthy invocation."""
        with self._lock:
            if was_probe:
                self._probe_in_flight = False
            if self.state == CircuitState.HALF_OPEN:
                logger.info("Circuit recovered, transitioning from HALF_OPEN to CLOSED", circuit=self.name)
                self.state = CircuitState.CLOSED
            self.failure_count = 0

    def _record_failure(self, was_probe: bool = False) -> None:
        """Increment failure count and trip circuit if threshold exceeded (a failed probe re-opens immediately)."""
        with self._lock:
            if was_probe:
                self._probe_in_flight = False
            self.failure_count += 1
            self.last_failure_time = time.time()
            if was_probe or self.failure_count >= self.config.failure_threshold:
                if self.state != CircuitState.OPEN:
                    self.tripped_count += 1
                    logger.critical("Circuit Breaker TRIPPED to OPEN state. Tool quarantined.", circuit=self.name, failures=self.failure_count)
                self.state = CircuitState.OPEN

    def get_status(self) -> Dict[str, Any]:
        """Return circuit breaker telemetry."""
        with self._lock:
            return {
                "name": self.name,
                "state": self.state.value,
                "failure_count": self.failure_count,
                "total_calls": self.total_calls,
                "tripped_count": self.tripped_count,
            }
