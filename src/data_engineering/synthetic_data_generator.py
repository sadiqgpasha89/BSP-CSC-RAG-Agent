"""Synthetic Telecom Dataset Generator & Data Seeder."""

import json
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

ACTIVE_OUTAGE_SEVERITIES = ("CRITICAL", "MAJOR")


class SyntheticDataGenerator:
    """Provides methods to load, validate, and access synthetic telecom assets.

    Files are cached and transparently reloaded when their modification time changes, so a request no longer
    re-parses the JSON catalogs several times per turn.
    """

    def __init__(self, data_root: Optional[str] = None):
        self.data_root = Path(data_root or settings.data_dir)
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _load_json(self, filename: str, default: Any) -> Any:
        path = self.data_root / filename
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            logger.warning("Data file missing", path=str(path))
            return default
        with self._lock:
            cached = self._cache.get(filename)
            if cached and cached[0] == mtime:
                return cached[1]
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._cache[filename] = (mtime, data)
            return data

    def load_subscribers(self) -> List[Dict[str, Any]]:
        """Load synthetic customer profiles."""
        return self._load_json("subscribers_catalog.json", [])

    def get_subscriber(self, subscriber_id: str) -> Optional[Dict[str, Any]]:
        """Find subscriber by ID or MSISDN."""
        for sub in self.load_subscribers():
            if sub["subscriber_id"] == subscriber_id or sub["msisdn"] == subscriber_id:
                return sub
        return None

    def load_network_alarms(self) -> List[Dict[str, Any]]:
        """Load synthetic network alarms."""
        return self._load_json("alarm_events.json", [])

    def load_network_topology(self) -> Dict[str, Any]:
        """Load the raw network topology document."""
        return self._load_json("network_topology.json", {"nodes": [], "edges": []})

    def get_active_alarms_for_sector(
        self, sector: str, severities: Optional[Iterable[str]] = None
    ) -> List[Dict[str, Any]]:
        """Filter active alarms impacting a geographic sector, optionally restricted to given severities."""
        allowed = {s.upper() for s in severities} if severities else None
        return [
            a for a in self.load_network_alarms()
            if a.get("sector") == sector
            and a.get("status") == "ACTIVE"
            and (allowed is None or str(a.get("severity", "")).upper() in allowed)
        ]


data_generator = SyntheticDataGenerator()
