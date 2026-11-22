"""RetentionAgent: save-offer formulation from the approved catalogue only.

The agent cannot invent concessions. It lists catalogue offers this customer qualifies for; applying one goes through
the gateway (customer confirmation, plus human approval above the autonomous value limit).
"""

from src.agents.base import AgentOutcome, BaseAgent
from src.agents.state import AgenticSessionState
from src.core.config import settings


class RetentionAgent(BaseAgent):
    NAME = "RetentionAgent"

    def run(self, state: AgenticSessionState, instruction: str) -> AgentOutcome:
        out = self.outcome()
        res = self.call(state, "catalog.list_offers")
        if not self.interpret(res, out):
            return self.finish(state, out)
        offers = res.data["offers"]
        if not offers:
            out.notices.append("I'm sorry to hear you're thinking of leaving. I don't have an automatic offer for your account, so I'm connecting you with a specialist who can discuss options.")
            state.escalate("Retention: customer at risk and no catalogue offer is available")
            return self.finish(state, out)
        best = max(offers, key=lambda o: o["value_usd"])
        out.notices.append("I'm sorry to hear that, and I'd like to keep you.")
        out.messages.append(f"You qualify for the {best['title']}: {best['description']}.")
        state.blackboard["retention_offer_id"] = best["offer_id"]
        res = self.call(state, "offer.apply", {"offer_id": best["offer_id"]})
        self.interpret(res, out)
        if res.status.value in ("CONFIRMATION_REQUIRED", "APPROVAL_REQUIRED"):
            out.notices.append("If you'd like it, confirm and I'll submit it" + (" for a supervisor to approve." if best["value_usd"] > settings.max_retention_offer_autonomous_usd else "."))
        return self.finish(state, out)


retention_agent = RetentionAgent()
