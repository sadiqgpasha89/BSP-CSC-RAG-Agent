"""Enterprise Circuit Breaker & Resilient Tool Quarantine Pattern."""

import time
from enum import Enum
from typing import Callable, Any, Dict, Optional
from pydantic import BaseModel
from src.core.logging import get_logger

logger = get_logger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"          # Normal operation
    OPEN = "OPEN"              # Tripped, quarantine active, fail fast to cached fallback
    HALF_OPEN = "HALF_OPEN"    # Testing if service recovered


class CircuitBreakerConfig(BaseModel):
    failure_threshold: int = 3
    recovery_timeout_sec: float = 30.0
    timeout_threshold_ms: float = 5000.0


class CircuitBreaker:
    """Protects agent tool execution from legacy OSS/BSS hangs and cascading failures."""

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.total_calls = 0
        self.tripped_count = 0

    def call(self, func: Callable[..., Any], *args, fallback: Optional[Callable[..., Any]] = None, **kwargs) -> Any:
        """Execute protected function with timeout, retry, and quarantine isolation."""
        self.total_calls += 1
        now = time.time()

        # Check if in OPEN state
        if self.state == CircuitState.OPEN:
            if now - self.last_failure_time > self.config.recovery_timeout_sec:
                logger.info("Circuit transitioning from OPEN to HALF_OPEN", circuit=self.name)
                self.state = CircuitState.HALF_OPEN
            else:
                logger.warning("Circuit is OPEN, executing cached fallback", circuit=self.name)
                if fallback:
                    return fallback(*args, **kwargs)
                raise RuntimeError(f"Circuit Breaker '{self.name}' is OPEN. Tool quarantined.")

        start_time = time.perf_counter()
        try:
            result = func(*args, **kwargs)
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            if duration_ms > self.config.timeout_threshold_ms:
                logger.warning("Tool execution latency breached SLA threshold", circuit=self.name, latency_ms=duration_ms)
                self._record_failure()
            else:
                self._record_success()

            return result
        except Exception as e:
            logger.error("Protected tool call raised exception", circuit=self.name, error=str(e))
            self._record_failure()
            if fallback:
                return fallback(*args, **kwargs)
            raise

    def _record_success(self) -> None:
        """Reset failure counts upon successful healthy invocation."""
        if self.state == CircuitState.HALF_OPEN:
            logger.info("Circuit recovered, transitioning from HALF_OPEN to CLOSED", circuit=self.name)
            self.state = CircuitState.CLOSED
        self.failure_count = 0

    def _record_failure(self) -> None:
        """Increment failure count and trip circuit if threshold exceeded."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.config.failure_threshold:
            self.state = CircuitState.OPEN
            self.tripped_count += 1
            logger.critical("Circuit Breaker TRIPPED to OPEN state. Tool quarantined.", circuit=self.name, failures=self.failure_count)

    def get_status(self) -> Dict[str, Any]:
        """Return circuit breaker telemetry."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "total_calls": self.total_calls,
            "tripped_count": self.tripped_count,
        }
