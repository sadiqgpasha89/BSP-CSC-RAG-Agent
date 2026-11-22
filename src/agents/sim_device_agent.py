"""SimDeviceAgent: SIM replacement, eSIM profile and number-port requests.

These are the highest-risk actions in the product. Every one needs authenticated MFA, a fresh step-up code, the
customer's explicit confirmation AND approval by a human operator (see config/policies.json). Urgency or "bypass"
language never relaxes that; it escalates to a human as a possible social-engineering attempt.
"""

import re

from src.agents.base import AgentOutcome, BaseAgent
from src.agents.entities import extract
from src.agents.state import AgenticSessionState

URGENCY_CUES = ("stranded", "emergency", "urgent", "immediately", "right now", "asap", "no time", "hurry")
BYPASS_RE = re.compile(r"\b(bypass|skip|without|no need (?:for|to)|don'?t need|do not need|waive|override)\b.{0,30}\b(pin|otp|code|verif\w*|2fa|mfa|security|identity|id check)\b", re.I)
SWAP_RE = re.compile(r"\b(swap|replace|replacement|new sim|lost (?:my )?(?:sim|phone)|damaged sim)\b", re.I)
ESIM_RE = re.compile(r"\b(esim|e-sim|qr code|download profile)\b", re.I)
PORT_RE = re.compile(r"\b(port(?:ing)?|transfer (?:my )?number|pac code|move my number)\b", re.I)


class SimDeviceAgent(BaseAgent):
    NAME = "SimDeviceAgent"

    def run(self, state: AgenticSessionState, instruction: str) -> AgentOutcome:
        raw = state.raw_utterance or ""
        text = (state.sanitized_utterance or "").lower()
        ent = extract(raw)
        out = self.outcome()

        if state.auth_context is None or state.auth_context.auth_level == "unauthenticated":
            out.needs_login = True               # identity first: don't even collect SIM/eSIM details from an unverified session
            return self.finish(state, out)
        if BYPASS_RE.search(raw):
            state.escalate("Customer asked to bypass identity verification for a SIM/eSIM/port request (possible social engineering)")
            out.notices.append("I can't skip identity verification for SIM, eSIM or number changes, whatever the circumstances. I've asked a specialist to help you with verified options.")
            return self.finish(state, out)
        if any(c in text for c in URGENCY_CUES):
            out.notices.append("I understand this is urgent. The same security checks still apply, but I'll move as fast as I can.")

        if ESIM_RE.search(raw):
            return self.finish(state, self._esim(state, ent, out))
        if PORT_RE.search(raw):
            return self.finish(state, self._port(state, raw, out))
        if SWAP_RE.search(raw):
            return self.finish(state, self._sim(state, ent, raw, out))
        out.clarification = "sim_request"
        out.notices.append("Do you want a replacement SIM, an eSIM, or to move your number to or from us?")
        return self.finish(state, out)

    def _sim(self, state, ent, raw, out: AgentOutcome) -> AgentOutcome:
        if not ent.get("iccid"):
            out.clarification = "iccid"
            out.notices.append("To replace your SIM, please send the ICCID printed on the new SIM card (it starts with 89).")
            return out
        res = self.call(state, "sim.replace", {"iccid": ent["iccid"], "reason": raw[:150] or "SIM replacement"})
        self.interpret(res, out)
        return out

    def _esim(self, state, ent, out: AgentOutcome) -> AgentOutcome:
        if not ent.get("device_eid"):
            out.clarification = "device_eid"
            out.notices.append("To prepare an eSIM, please send your device's EID (Settings > About > EID).")
            return out
        res = self.call(state, "esim.issue", {"device_eid": ent["device_eid"], "reason": "eSIM request"})
        self.interpret(res, out)
        return out

    def _port(self, state, raw, out: AgentOutcome) -> AgentOutcome:
        direction = "out" if re.search(r"\b(out|leave|leaving|to another|competitor)\b", raw, re.I) else "in"
        res = self.call(state, "port.initiate", {"direction": direction, "reason": raw[:150] or "Number port"})
        self.interpret(res, out)
        return out


sim_device_agent = SimDeviceAgent()
