"""NetworkAgent: outage awareness, subscriber-level diagnostics and a subscriber-level signalling refresh.

Read-only against the network except for the confirmed per-subscriber soft reset. It never changes infrastructure
(plan §12: no autonomous network control).
"""

import re
from typing import Any, Dict

from src.agents.base import AgentOutcome, BaseAgent
from src.agents.state import AgenticSessionState
from src.tools.gateway import tool_gateway

FIBER_RE = re.compile(r"\b(fiber|fibre|broadband|otdr|red light|flashing red|pon|router|wifi|wi-fi)\b", re.I)
EXPLICIT_TEST_RE = re.compile(r"\b(otdr|run (?:a |the )?(?:line |network )?test|diagnos\w*|test my line)\b", re.I)
ATTACH_RE = re.compile(r"(0x4f|attach|no service|no signal|can'?t (?:make|receive) calls?|not registering)", re.I)


class NetworkAgent(BaseAgent):
    NAME = "NetworkAgent"

    def outage_status(self, state: AgenticSessionState) -> Dict[str, Any]:
        # Ingress check, not a reasoning step: it must not consume the turn's step budget. Still gateway-enforced and audited.
        res = tool_gateway.invoke(self.ctx(state), "network.check_outage", {})
        return res.data if res.ok else {"outage_confirmed": False, "alarms": []}

    def run(self, state: AgenticSessionState, instruction: str) -> AgentOutcome:
        raw = state.sanitized_utterance or ""
        out = self.outcome()
        outage = self.call(state, "network.check_outage")
        if self.interpret(outage, out) and outage.data["outage_confirmed"]:
            a = outage.data["alarms"][0]
            out.messages.append(f"There is a known {str(a.get('type') or 'network event').replace('_', ' ').lower()} affecting your area ({outage.data['sector']}). "
                                f"Our crews are working on it; estimated restoration is {a.get('estimated_restoration_time')}.")
            out.data["outage"] = True
            if not EXPLICIT_TEST_RE.search(raw):
                return self.finish(state, out)
        if out.status == "failed":
            return self.finish(state, out)

        stype = "broadband" if FIBER_RE.search(raw) else "mobile"
        diag = self.call(state, "network.run_diagnostics", {"service_type": stype})
        if not self.interpret(diag, out):
            return self.finish(state, out)
        d = diag.data
        line, tower = d.get("line"), d["tower"]
        if line and line.get("status") == "NO_BROADBAND_SERVICE":
            out.messages.append("I can't find a broadband service on your account, so I couldn't test a fibre line.")
        elif line and line.get("status") == "FIBER_CUT_DETECTED":
            out.messages.append(f"The optical line test found a fibre break about {line['fault_distance_meters']} m from the distribution point. A technician needs to repair it.")
            t = self.call(state, "ticket.create", {"subtype": "technical", "category": "fibre_fault", "summary": f"Fibre cut detected {line['fault_distance_meters']} m from the hub; technician dispatch needed",
                                                    "signals": {"service_down": True}})
            self.interpret(t, out)
        elif line:
            out.messages.append(f"The optical line test found your fibre line healthy: {line['summary']}")
        if tower.get("status") in ("CRITICAL_ALARM", "DEGRADED_WARNING"):
            out.messages.append(f"Our monitoring shows {tower['name']} is reporting {tower['status'].replace('_', ' ').lower()}" + (f": {tower['root_cause']}." if tower.get("root_cause") else "."))
        elif tower.get("status") == "OPERATIONAL" and not line:
            out.messages.append(f"Our monitoring shows {tower['name']} operating normally with no active alarms.")
        if ATTACH_RE.search(raw) and stype == "mobile":
            r = self.call(state, "network.soft_reset")
            self.interpret(r, out)
        return self.finish(state, out)


network_agent = NetworkAgent()
