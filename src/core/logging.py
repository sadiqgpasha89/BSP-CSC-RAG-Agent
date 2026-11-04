"""Structured Logging with Distributed Trace-ID Propagation."""

import logging
import sys
from typing import Optional

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog and standard logging for production-grade structured JSON output."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str, trace_id: Optional[str] = None):
    """Return a *lazy* structlog logger.

    The previous implementation called ``.bind()`` at import time, which forces structlog to build the
    logger from its *default* configuration -- i.e. before ``configure_logging`` ever ran -- so JSON output and
    level filtering silently never applied to module-level loggers. Returning the lazy proxy defers
    configuration lookup to the first log call. Per-request correlation is supplied through context variables
    (see ``bind_trace_context``) instead of a random id frozen per module.
    """
    if trace_id:
        return structlog.get_logger(name, trace_id=trace_id)
    return structlog.get_logger(name)


def bind_trace_context(trace_id: str, session_id: Optional[str] = None) -> None:
    """Attach trace/session identifiers to every log line emitted by the current execution context."""
    values = {"trace_id": trace_id}
    if session_id:
        values["session_id"] = session_id
    bind_contextvars(**values)


def clear_trace_context() -> None:
    """Drop request-scoped log context (call in a ``finally`` block)."""
    clear_contextvars()
