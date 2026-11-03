"""Synthetic Telecom Dataset Generator & Data Seeder."""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from src.core.logging import get_logger

logger = get_logger(__name__)


class SyntheticDataGenerator:
    """Provides methods to load, validate, and access synthetic telecom assets."""

    def __init__(self, data_root: str = "data"):
        self.data_root = Path(data_root)

    def load_subscribers(self) -> List[Dict[str, Any]]:
        """Load synthetic customer profiles."""
        path = self.data_root / "subscribers_catalog.json"
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_subscriber(self, subscriber_id: str) -> Optional[Dict[str, Any]]:
        """Find subscriber by ID or MSISDN."""
        subscribers = self.load_subscribers()
        for sub in subscribers:
            if sub["subscriber_id"] == subscriber_id or sub["msisdn"] == subscriber_id:
                return sub
        return None

    def load_network_alarms(self) -> List[Dict[str, Any]]:
        """Load synthetic network alarms."""
        path = self.data_root / "alarm_events.json"
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_active_alarms_for_sector(self, sector: str) -> List[Dict[str, Any]]:
        """Filter active alarms impacting a geographic sector."""
        alarms = self.load_network_alarms()
        return [a for a in alarms if a.get("sector") == sector and a.get("status") == "ACTIVE"]


data_generator = SyntheticDataGenerator()
