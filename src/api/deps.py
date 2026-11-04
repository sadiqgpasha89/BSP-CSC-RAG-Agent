"""Shared FastAPI dependencies (operator authentication)."""

import hmac
from typing import Optional

from fastapi import Header, HTTPException, status

from src.core.config import settings


def require_operator(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Gate privileged / state-changing endpoints (OSS resets, HITL resolution, self-healing, PII-bearing queues).

    * Development: open, so the demo console works without credentials.
    * Any other environment: ``X-API-Key`` must match ``ADMIN_API_KEY`` (constant-time comparison). Settings
      validation guarantees the key is configured outside development, so the check can never fail open.
    """
    if settings.is_development and not settings.admin_api_key:
        return
    expected = settings.admin_api_key or ""
    if not x_api_key or not expected or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Operator credentials required")
