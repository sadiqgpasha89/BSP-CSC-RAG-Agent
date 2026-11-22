"""Shared types for enterprise integration adapters.

Every adapter in ``src/tools`` is a *sandbox implementation* of a stable internal interface: it is deterministic,
labelled ``mock=True`` in provenance, and replaceable by a real CRM / BSS / OCS / payment / ticketing client without
changing any agent or workflow (see docs/TSD.md §7 for the replacement matrix).
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from src.core.config import settings


class AdapterError(Exception):
    """A definitive, business-level refusal from a backend (maps to ToolStatus.FAILED / NOT_FOUND)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class NotFoundError(AdapterError):
    def __init__(self, what: str):
        # Deliberately identical for "does not exist" and "belongs to someone else": no cross-customer existence leak.
        super().__init__("NOT_FOUND", f"{what} not found for this account.")


class TransientError(AdapterError):
    """Safe-to-retry failure for *read* operations (never raised after a side effect happened)."""

    def __init__(self, message: str = "Backend temporarily unavailable."):
        super().__init__("TRANSIENT", message)


class ProviderTimeout(Exception):
    """The backend did not answer. For side-effecting calls the real outcome is UNKNOWN and must be reconciled."""

    def __init__(self, message: str = "Backend timed out.", record_id: Optional[str] = None):
        super().__init__(message)
        self.record_id = record_id  # id of the (possibly executed) record, so the caller can reconcile instead of retrying


class Provenance(BaseModel):
    system: str                    # e.g. "OCS", "BSS-Billing", "Payment-Gateway", "KB"
    kind: str = "LIVE_API"         # LIVE_API | RAG | POLICY | DERIVED
    record_id: Optional[str] = None
    mock: bool = True
    fetched_at: float = Field(default_factory=time.time)

    def label(self) -> str:
        suffix = " (sandbox)" if self.mock else ""
        rid = f" · {self.record_id}" if self.record_id else ""
        return f"{self.system}{suffix}{rid}"


def load_sandbox_json(relative: str, default: Any = None) -> Any:
    path = Path(settings.data_dir) / relative
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default if default is not None else {}


def mask_value(value: str, keep: int = 4) -> str:
    return "*" * max(0, len(value) - keep) + value[-keep:] if value else value
