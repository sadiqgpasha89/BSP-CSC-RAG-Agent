"""SalesAgent: plan/add-on discovery, usage-based recommendations, comparisons, and confirmed plan/add-on changes."""

import re
from typing import List

from src.agents.base import AgentOutcome, BaseAgent
from src.agents.entities import extract, find_plans
from src.agents.state import AgenticSessionState

COMPARE_RE = re.compile(r"\b(compare|difference|vs|versus|better)\b", re.I)
RECOMMEND_RE = re.compile(r"\b(recommend|suggest|best plan|which plan|right plan|cheaper|save money|upgrade|downgrade|need more data|run out)\b", re.I)
CHANGE_RE = re.compile(r"\b(switch|move|change|upgrade|downgrade|go to|shift)\b", re.I)
ADDON_RE = re.compile(r"\b(add|activate|buy|get|enable|remove|deactivate|cancel)\b.*\b(roaming|boost|passport|calling|add-?on|pack)\b|\b(roaming|data boost)\b", re.I)
REMOVE_RE = re.compile(r"\b(remove|deactivate|cancel|stop|disable)\b", re.I)


class SalesAgent(BaseAgent):
    NAME = "SalesAgent"

    def run(self, state: AgenticSessionState, instruction: str) -> AgentOutcome:
        text = state.sanitized_utterance or ""
        ent = extract(state.raw_utterance or text)
        out = self.outcome()
        plans = find_plans(state.raw_utterance or text)

        if ent.get("addon_code") and ADDON_RE.search(text):
            return self.finish(state, self._addon(state, ent["addon_code"], bool(REMOVE_RE.search(text)), out))
        if ent.get("addon_code"):
            return self.finish(state, self._describe_addon(state, ent["addon_code"], out))
        if len(plans) >= 2 and COMPARE_RE.search(text):
            return self.finish(state, self._compare(state, plans, out))
        if ent.get("plan_code") and CHANGE_RE.search(text):
            return self.finish(state, self._change_plan(state, ent["plan_code"], out))
        if ent.get("plan_code") and not RECOMMEND_RE.search(text):
            return self.finish(state, self._describe_plan(state, ent["plan_code"], out))
        return self.finish(state, self._recommend(state, text, out))

    def _recommend(self, state, text, out: AgentOutcome) -> AgentOutcome:
        goal = "performance" if re.search(r"\b(faster|speed|performance|more data|run out)\b", text, re.I) else "value"
        res = self.call(state, "catalog.recommend", {"goal": goal})
        if not self.interpret(res, out):
            return out
        d = res.data
        recs = [r for r in d["recommendations"] if not r["is_current"]][:3]
        if d.get("measured_monthly_data_gb") is not None:
            out.messages.append(f"You used about {d['measured_monthly_data_gb']} GB over the last 30 days.")
        if not recs:
            out.notices.append("Your current plan already looks like the best fit for your usage.")
            return out
        for r in recs:
            p = r["plan"]
            delta = r["monthly_delta_usd"]
            change = "the same price" if abs(delta) < 0.005 else f"${abs(delta):.2f} {'more' if delta > 0 else 'less'} per month"
            out.messages.append(f"{p['name']} is ${p['monthly_fee_usd']:.2f}/month ({change}): {', '.join(p['highlights'][:2])}. {r['reason']}")
        out.notices.append("Tell me which one you'd like and I'll set it up after you confirm.")
        return out

    def _compare(self, state, plans: List[str], out: AgentOutcome) -> AgentOutcome:
        res = self.call(state, "catalog.compare_plans", {"plan_codes": plans[:4]})
        if self.interpret(res, out):
            for p in res.data["plans"]:
                out.messages.append(f"{p['name']}: ${p['monthly_fee_usd']:.2f}/month; {', '.join(p['highlights'][:3])}.")
        return out

    def _change_plan(self, state, plan_code: str, out: AgentOutcome) -> AgentOutcome:
        elig = self.call(state, "catalog.check_eligibility", {"target_type": "plan", "code": plan_code})
        if not self.interpret(elig, out):
            return out
        e = elig.data
        if not e["eligible"]:
            out.notices.append("That plan is not available for your account: " + " ".join(e["reasons"]))
            return out
        res = self.call(state, "plan.change", {"plan_code": plan_code})
        self.interpret(res, out)
        return out

    def _describe_plan(self, state, plan_code: str, out: AgentOutcome) -> AgentOutcome:
        res = self.call(state, "catalog.get_plan", {"plan_code": plan_code})
        if self.interpret(res, out):
            p = res.data["plan"]
            out.messages.append(f"{p['name']} costs ${p['monthly_fee_usd']:.2f}/month: {', '.join(p['highlights'])}.")
        return out

    def _describe_addon(self, state, addon_code: str, out: AgentOutcome) -> AgentOutcome:
        res = self.call(state, "catalog.list_addons")
        if self.interpret(res, out):
            a = next((x for x in res.data["addons"] if x["addon_code"] == addon_code), None)
            if a:
                out.messages.append(f"{a['name']} costs ${a['price_usd']:.2f} {a['period'].replace('_', ' ')}." +
                                    ("" if a["eligible"] else f" It is not available on your account: {a['ineligible_reason']}"))
        return out

    def _addon(self, state, addon_code: str, remove: bool, out: AgentOutcome) -> AgentOutcome:
        op = "deactivate" if remove else "activate"
        elig = self.call(state, "catalog.check_eligibility", {"target_type": "addon", "code": addon_code, "operation": op})
        if not self.interpret(elig, out):
            return out
        if not elig.data["eligible"]:
            out.notices.append("That add-on is not available for your account: " + " ".join(elig.data["reasons"]))
            return out
        res = self.call(state, "addon.change", {"addon_code": addon_code, "operation": op})
        self.interpret(res, out)
        return out


sales_agent = SalesAgent()
