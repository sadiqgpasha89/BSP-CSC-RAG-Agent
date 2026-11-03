"""Structured Logging with Distributed Trace-ID Propagation."""

import logging
import sys
import uuid
from typing import Any, Dict, Optional
import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog and standard logging for production-grade structured JSON output."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str, trace_id: Optional[str] = None):
    """Obtain a structured logger pre-bound with module name and optional trace_id."""
    logger = structlog.get_logger(name)
    if trace_id:
        return logger.bind(trace_id=trace_id)
    return logger.bind(trace_id=f"trc-{uuid.uuid4().hex[:12]}")
