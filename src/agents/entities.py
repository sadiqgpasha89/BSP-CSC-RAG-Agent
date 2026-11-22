"""Deterministic entity extraction used by agents to build *typed tool arguments*.

Anything the model/regex extracts is only a proposal: the gateway re-validates it, and catalogue codes are matched
against the real catalogue, so an invented code can never reach a backend.
"""

import re
from typing import Any, Dict, List, Optional

from src.tools.catalog_adapter import catalog_adapter

INVOICE_RE = re.compile(r"\bINV-[A-Za-z0-9\-]+\b", re.I)
CASE_RE = re.compile(r"\bTCK-\d+\b", re.I)
ORDER_RE = re.compile(r"\bORD-\d+\b", re.I)
PAYMENT_RE = re.compile(r"\bPAY-[A-Z0-9]+\b")
ICCID_RE = re.compile(r"\b89\d{16,18}\b")
EID_RE = re.compile(r"\bEID-\d{10,20}\b|\b\d{32}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
AMOUNT_RE = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")
CARD_RE = re.compile(r"(?:ending|ends|last\s*4)\D{0,6}(\d{4})", re.I)
OTP_RE = re.compile(r"^\D*(\d{6})\D*$")

PLAN_ALIASES = {
    "5g ultra max": "5G_ULTRA_MAX_2026", "ultra max": "5G_ULTRA_MAX_2026", "5g plus 50": "5G_PLUS_50_2026", "plus 50": "5G_PLUS_50_2026",
    "5g plus": "5G_PLUS_50_2026", "freedom unlimited": "FREEDOM_UNLIMITED_2018",
}
ADDON_ALIASES = {"roaming": "ROAM_PASSPORT_2026", "wanderlust": "ROAM_PASSPORT_2026", "passport": "ROAM_PASSPORT_2026", "data boost": "DATA_BOOST_10GB",
                 "boost": "DATA_BOOST_10GB", "international calling": "INTL_CALLING_PACK", "intl calling": "INTL_CALLING_PACK"}


def _catalog_names() -> Dict[str, str]:
    names: Dict[str, str] = {}
    try:
        for p in catalog_adapter.list_plans()["plans"]:
            names[p["name"].lower()] = p["plan_code"]
    except Exception:
        pass
    return names


def find_plan(text: str) -> Optional[str]:
    low = text.lower()
    for name, code in {**_catalog_names(), **PLAN_ALIASES}.items():
        if name in low:
            return code
    m = re.search(r"\b([A-Z0-9]+_[A-Z0-9_]+_\d{4})\b", text)
    return m.group(1) if m else None


def find_plans(text: str) -> List[str]:
    low = text.lower()
    found: List[str] = []
    for name, code in {**_catalog_names(), **PLAN_ALIASES}.items():
        if name in low and code not in found:
            found.append(code)
    return found


def find_addon(text: str) -> Optional[str]:
    low = text.lower()
    for name, code in ADDON_ALIASES.items():
        if name in low:
            return code
    return None


def find_pack(text: str) -> Optional[str]:
    m = re.search(r"\bPK_\d+\b", text, re.I)
    return m.group(0).upper() if m else None


def extract(text: str) -> Dict[str, Any]:
    ent: Dict[str, Any] = {}
    for key, rx in (("invoice_id", INVOICE_RE), ("case_id", CASE_RE), ("order_id", ORDER_RE), ("payment_id", PAYMENT_RE),
                    ("iccid", ICCID_RE), ("device_eid", EID_RE), ("email", EMAIL_RE)):
        m = rx.search(text)
        if m:
            ent[key] = m.group(0).upper() if key in ("case_id", "order_id") else m.group(0)
    am = AMOUNT_RE.search(text)
    if am:
        ent["amount"] = float(am.group(1))
    cm = CARD_RE.search(text)
    if cm:
        ent["card_last4"] = cm.group(1)
    for key, fn in (("plan_code", find_plan), ("addon_code", find_addon), ("pack_id", find_pack)):
        v = fn(text)
        if v:
            ent[key] = v
    return ent
