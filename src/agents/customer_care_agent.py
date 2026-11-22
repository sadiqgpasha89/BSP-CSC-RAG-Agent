"""CustomerCareAgent: account & usage questions, cases/complaints, contact details and preferences."""

import re
from typing import Any, Dict, List

from src.agents.base import AgentOutcome, BaseAgent
from src.agents.entities import extract
from src.agents.state import AgenticSessionState

COMPLAINT_RE = re.compile(r"\b(complain|complaint|unhappy|not satisfied|poor service|raise a ticket|open a ticket|log a ticket|escalate)\b", re.I)
CASE_STATUS_RE = re.compile(r"\b(case|ticket|complaint)\b.*\b(status|update|progress|where)\b|\b(status|update on)\b.*\b(case|ticket|complaint)\b|\bTCK-\d+\b", re.I)
USAGE_RE = re.compile(r"\b(usage|data left|remaining|balance|how much data|used|allowance|minutes|validity|expire)\b", re.I)
PROFILE_RE = re.compile(r"\b(change|update|set)\b.*\b(email|e-mail|address|name)\b", re.I)
OPTOUT_RE = re.compile(r"\b(stop|opt[- ]?out|unsubscribe|do not|don't)\b.*\b(marketing|promo|promotional|offers|messages|sms|whatsapp|emails?)\b", re.I)
OPTIN_RE = re.compile(r"\b(i agree|i consent|yes[, ]+(?:you may|you can)|opt[- ]?in)\b", re.I)
PROFILE_FIELD_RE = re.compile(r"\b(email|e-mail|address|name)\b", re.I)


class CustomerCareAgent(BaseAgent):
    NAME = "CustomerCareAgent"

    def run(self, state: AgenticSessionState, instruction: str) -> AgentOutcome:
        text = state.sanitized_utterance or ""
        ent = extract(state.raw_utterance or text)
        out = self.outcome()

        if OPTOUT_RE.search(text):
            return self.finish(state, self._opt_out(state, text, out))
        if PROFILE_RE.search(text):
            return self.finish(state, self._profile_update(state, text, ent, out))
        if ent.get("case_id") or CASE_STATUS_RE.search(text):
            return self.finish(state, self._case_status(state, ent, out))
        if COMPLAINT_RE.search(text):
            return self.finish(state, self._complaint(state, text, out))
        return self.finish(state, self._account_overview(state, text, out))

    # ------------------------------------------------------------------ usage / account
    def _account_overview(self, state, text, out: AgentOutcome) -> AgentOutcome:
        bal = self.call(state, "usage.get_balance")
        if not self.interpret(bal, out):
            return out
        b = bal.data
        lines: List[str] = []
        if b["account_type"] == "prepaid":
            lines.append(f"Your prepaid balance is ${b['balance_usd']:.2f}" + (f", valid until {b['balance_expires']}." if b.get("balance_expires") else "."))
            if b.get("data_gb", {}).get("remaining") is not None:
                lines.append(f"You have {b['data_gb']['remaining']} GB of data remaining.")
        else:
            d = b["data_gb"]
            if d.get("allowance"):
                lines.append(f"This cycle you have used {d['used']} GB of {d['allowance']} GB ({d['remaining']} GB left); it resets on {b['cycle_resets']}.")
            else:
                lines.append(f"This cycle you have used {d['used']} GB of data on your unlimited plan; it resets on {b['cycle_resets']}.")
            if b.get("unbilled_usage_usd"):
                lines.append(f"Your unbilled usage so far is ${b['unbilled_usage_usd']:.2f}.")
        an = self.call(state, "usage.get_anomalies")
        if an.ok and an.data.get("anomalies"):
            a = an.data["anomalies"][0]
            lines.append(f"One thing stands out: about {a['days_ago']} days ago you used {a['data_gb']} GB in a day, around {a['ratio']}x your normal {a['baseline_median_gb']} GB.")
        out.messages.extend(lines)
        return out

    # ------------------------------------------------------------------ cases
    def _case_status(self, state, ent: Dict[str, Any], out: AgentOutcome) -> AgentOutcome:
        if ent.get("case_id"):
            res = self.call(state, "ticket.get", {"case_id": ent["case_id"]})
            if self.interpret(res, out):
                c = res.data["case"] if "case" in res.data else res.data
                out.messages.append(f"Case {c['case_id']} ({c['subtype'].replace('_', ' ')}) is {c['status'].replace('_', ' ').lower()}. "
                                    f"It is with {c['queue'].replace('_', ' ')} and the target response time is {c['sla_hours']} hours.")
            return out
        res = self.call(state, "ticket.list")
        if self.interpret(res, out):
            cases = res.data["cases"]
            if not cases:
                out.notices.append("You have no open cases with us.")
            else:
                out.messages.append("Your cases: " + "; ".join(f"{c['case_id']} is {c['status'].replace('_', ' ').lower()}" for c in cases[:5]) + ".")
        return out

    def _complaint(self, state, text, out: AgentOutcome) -> AgentOutcome:
        res = self.call(state, "ticket.create", {"subtype": "complaint", "category": "service", "summary": text[:900] or "Customer complaint",
                                                  "signals": {"customer_requested_escalation": True}})
        self.interpret(res, out)
        if res.status.value == "CONFIRMATION_REQUIRED":
            out.data["case_preview"] = True
        return out

    # ------------------------------------------------------------------ profile & preferences
    def _profile_update(self, state, text, ent, out: AgentOutcome) -> AgentOutcome:
        f = PROFILE_FIELD_RE.search(text)
        fld = {"e-mail": "email"}.get(f.group(1).lower(), f.group(1).lower()) if f else None
        value = ent.get("email") if fld == "email" else None
        if fld is None or not value:
            out.clarification = "profile_value"
            out.notices.append("Which detail would you like to change, and what is the new value? For example: \"change my email to name@example.com\".")
            return out
        res = self.call(state, "profile.update", {"field": fld, "value": value})
        self.interpret(res, out)
        return out

    def _opt_out(self, state, text, out: AgentOutcome) -> AgentOutcome:
        args: Dict[str, Any] = {"purpose": "marketing", "granted": False}
        for ch in ("sms", "whatsapp", "email"):
            if ch in text.lower():
                args = {"channel": ch, "enabled": False}
                break
        res = self.call(state, "preferences.update", args)
        self.interpret(res, out)
        return out


customer_care_agent = CustomerCareAgent()
