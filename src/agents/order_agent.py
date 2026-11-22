"""OrderAgent: order / installation / delivery tracking and appointments (read-only; changes go to a human)."""

from src.agents.base import AgentOutcome, BaseAgent
from src.agents.entities import extract
from src.agents.state import AgenticSessionState
import re

RESCHEDULE_RE = re.compile(r"\b(reschedule|change|move|cancel)\b.*\b(appointment|visit|installation|technician)\b", re.I)


class OrderAgent(BaseAgent):
    NAME = "OrderAgent"

    def run(self, state: AgenticSessionState, instruction: str) -> AgentOutcome:
        text = state.sanitized_utterance or ""
        ent = extract(state.raw_utterance or text)
        out = self.outcome()
        if ent.get("order_id"):
            res = self.call(state, "order.get", {"order_id": ent["order_id"]})
            if self.interpret(res, out):
                out.messages.append(self._line(res.data.get("order", res.data)))
        else:
            res = self.call(state, "order.list")
            if self.interpret(res, out):
                orders = res.data["orders"]
                if not orders:
                    out.notices.append("I don't see any orders on your account.")
                for o in orders[:3]:
                    out.messages.append(self._line(o))
            appt = self.call(state, "order.appointments")
            if appt.ok:
                for a in appt.data.get("appointments", [])[:2]:
                    out.messages.append(f"Technician appointment {a['appointment_id']} is booked for {a['date']} ({a['window']}).")
        if RESCHEDULE_RE.search(text):
            state.escalate("Customer wants to change an appointment/order; handled by a human scheduler")
            out.notices.append("Changing an appointment needs our scheduling team, so I have passed it to them with the details.")
        return self.finish(state, out)

    @staticmethod
    def _line(o) -> str:
        return f"Order {o['order_id']} ({o['description']}) is {o['latest_stage'].replace('_', ' ').lower()}" + (f", status {o['status'].replace('_', ' ').lower()}." if o.get('status') else ".")


order_agent = OrderAgent()
