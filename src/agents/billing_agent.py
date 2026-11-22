"""BillingAgent: explains invoices, registers disputes, takes bill payments and prepaid recharges.

It NEVER credits money. A dispute is registered with evidence and routed to a human specialist; credits are a
human-approved action outside the agent's permissions (policy pack: ``billing.credit`` autonomous limit = 0).
"""

import re
from typing import Any, Dict, Optional

from src.agents.base import AgentOutcome, BaseAgent
from src.agents.entities import extract
from src.agents.state import AgenticSessionState

DISPUTE_RE = re.compile(r"\b(dispute|wrong|incorrect|overcharg\w*|charged twice|double[- ]charged|unexpected|unauthori[sz]ed|refund|credit|reverse|shouldn'?t|not mine|didn'?t use|never used)\b", re.I)
PAY_RE = re.compile(r"\bpagar (?:mi |la )?factura\b|बिल (?:भरना|भरें)|\b(pay|payment|settle|clear)\b.*\b(bill|invoice|due|outstanding|balance)\b|\bpay (?:my|the) (?:bill|invoice)\b|\bpay now\b", re.I)
RECHARGE_RE = re.compile(r"\b(recharge|top[- ]?up|topup|add (?:data|credit|balance)|buy (?:a )?(?:pack|data)|recargar?|रिचार्ज)\b|रिचार्ज", re.I)
STATUS_RE = re.compile(r"\b(payment status|did (?:my )?payment|payment (?:go|went) through|pending payment|failed payment)\b", re.I)
MOST_RE = re.compile(r"\b(?:the )?(?:roaming|international|frankfurt|london|premium)\b", re.I)


class BillingAgent(BaseAgent):
    NAME = "BillingAgent"

    def run(self, state: AgenticSessionState, instruction: str) -> AgentOutcome:
        text = state.sanitized_utterance or ""
        ent = extract(state.raw_utterance or text)
        out = self.outcome()
        if ent.get("payment_id") or STATUS_RE.search(text):
            return self.finish(state, self._payment_status(state, ent, out))
        if RECHARGE_RE.search(text):
            return self.finish(state, self._recharge(state, ent, out))
        if PAY_RE.search(text):
            return self.finish(state, self._pay_bill(state, ent, out))
        if DISPUTE_RE.search(text):
            return self.finish(state, self._dispute(state, ent, text, out))
        return self.finish(state, self._explain(state, ent, out))

    # ------------------------------------------------------------------ explain
    def _explain(self, state, ent, out: AgentOutcome, *, quiet: bool = False) -> AgentOutcome:
        res = self.call(state, "billing.explain_charges", {"invoice_id": ent.get("invoice_id", "latest")})
        if not self.interpret(res, out):
            return out
        d = res.data
        state.blackboard["invoice"] = {"invoice_id": d["invoice_id"], "disputable": d["disputable_line_ids"], "status": d["status"], "total_usd": d["total_usd"]}
        if quiet:
            return out
        out.messages.append(f"Invoice {d['invoice_id']} covers {d['period']} and totals ${d['total_usd']:.2f} (status: {d['status'].replace('_', ' ').lower()}, due {d['due']}).")
        for ln in d["lines"]:
            note = ln.get("evidence", {}).get("note")
            out.messages.append(f"- {ln['description']}: ${ln['amount_usd']:.2f} (source {ln['evidence']['source']}, ref {ln['evidence']['ref']})" + (f". {note}" if note else ""))
        if not d["totals_match"]:
            out.notices.append("The itemised lines do not add up to the invoice total, so I have asked a billing specialist to review it.")
            state.escalate("Invoice lines do not reconcile with invoice total")
        elif d["disputable_line_ids"]:
            out.notices.append("A couple of these charges look worth a second look. Tell me if you want me to dispute them and I'll register it for a specialist to review.")
        return out

    # ------------------------------------------------------------------ dispute
    def _dispute(self, state, ent, text, out: AgentOutcome) -> AgentOutcome:
        self._explain(state, ent, out, quiet=True)
        inv = state.blackboard.get("invoice")
        if out.status == "failed" or not inv:
            return out
        res = self.call(state, "billing.explain_charges", {"invoice_id": inv["invoice_id"]})
        lines = res.data["lines"] if res.ok else []
        disputable = [l for l in lines if l["line_id"] in inv["disputable"]]
        if ent.get("amount") is not None:
            matched = [l for l in disputable if abs(l["amount_usd"] - ent["amount"]) < 0.005]
            total_disputable = sum(l["amount_usd"] for l in disputable)
            if not matched and ent["amount"] > total_disputable + 0.005:
                out.notices.append(f"I can't issue a credit of ${ent['amount']:.2f} myself, and the invoice evidence doesn't support that amount. I've asked a billing specialist to review your request.")
                state.escalate(f"Customer requests ${ent['amount']:.2f}, above the evidence-backed disputed amount (${total_disputable:.2f}); only a human can decide credits")
                return out
            disputable = matched or disputable
        if not disputable:
            out.notices.append(f"I checked each line of invoice {inv['invoice_id']} against its source records and they all match your plan and usage, so there is nothing I can dispute automatically.")
            state.escalate("Customer disputes charges that are supported by invoice evidence; needs specialist review")
            return out
        res = self.call(state, "billing.register_dispute", {"invoice_id": inv["invoice_id"], "line_ids": [l["line_id"] for l in disputable],
                                                             "reason": text[:450] or "Customer disputes these charges"})
        self.interpret(res, out)
        if res.status.value == "CONFIRMATION_REQUIRED":
            out.messages.append("The charges that look disputable: " + "; ".join(f"{l['description']} (${l['amount_usd']:.2f})" for l in disputable) + ".")
            out.notices.append("I can't credit your account myself, but I can register the dispute with this evidence for a billing specialist.")
        return out

    # ------------------------------------------------------------------ pay a bill
    def _pay_bill(self, state, ent, out: AgentOutcome) -> AgentOutcome:
        self._explain(state, ent, out, quiet=True)
        inv = state.blackboard.get("invoice")
        if out.status == "failed" or not inv:
            return out
        if inv["status"] == "PAID":
            out.notices.append(f"Invoice {inv['invoice_id']} is already paid, so there is nothing to pay.")
            return out
        if inv["status"].startswith("DISPUTED"):
            out.notices.append("This invoice is under dispute. Paying it does not stop the dispute review.")
        method = self._choose_method(state, ent, out)
        if method is None:
            return out
        res = self.call(state, "payment.initiate", {"amount_usd": inv["total_usd"], "method_id": method, "purpose": "BILL_PAYMENT", "reference": inv["invoice_id"]})
        self.interpret(res, out)
        return out

    # ------------------------------------------------------------------ recharge
    def _recharge(self, state, ent, out: AgentOutcome) -> AgentOutcome:
        rec = self.call(state, "catalog.recommend_recharge")
        if not self.interpret(rec, out):
            return out
        packs = {p["pack_id"]: p for p in rec.data["all_packs"]}
        pack = packs.get(ent.get("pack_id", "")) or next((p for p in packs.values() if ent.get("amount") is not None and abs(p["amount_usd"] - ent["amount"]) < 0.005), None)
        if pack is None:
            r = rec.data["recommended_pack"]
            out.messages.append(f"Based on your last 30 days of use (about {rec.data['estimated_monthly_need_gb']} GB a month), the closest pack is {r['label']} for ${r['amount_usd']:.2f}.")
            out.messages.append("Other packs: " + "; ".join(f"{p['label']} ${p['amount_usd']:.2f}" for p in packs.values() if p["pack_id"] != r["pack_id"]) + ".")
            out.notices.append("Tell me which one you want.")
            out.clarification = "recharge_pack"
            return out
        method = self._choose_method(state, ent, out)
        if method is None:
            return out
        res = self.call(state, "payment.initiate", {"amount_usd": pack["amount_usd"], "method_id": method, "purpose": "RECHARGE", "reference": pack["pack_id"]})
        self.interpret(res, out)
        return out

    # ------------------------------------------------------------------ payment status
    def _payment_status(self, state, ent, out: AgentOutcome) -> AgentOutcome:
        pid = ent.get("payment_id") or (state.blackboard.get("last_payment_id"))
        if not pid:
            out.notices.append("Which payment do you mean? Please share the payment reference (it starts with PAY-).")
            out.clarification = "payment_id"
            return out
        res = self.call(state, "payment.reconcile", {"payment_id": pid})
        if self.interpret(res, out):
            out.messages.append(payment_sentence(res.data))
        return out

    # ------------------------------------------------------------------ helpers
    def _choose_method(self, state, ent, out: AgentOutcome) -> Optional[str]:
        res = self.call(state, "payment.list_methods")
        if not self.interpret(res, out):
            return None
        methods = res.data["methods"]
        if not methods:
            out.notices.append("You don't have a saved payment method. Please add one in the app and I'll take it from there.")
            return None
        if ent.get("card_last4"):
            for m in methods:
                if ent["card_last4"] in m["label"]:
                    return m["method_id"]
        return methods[0]["method_id"]


def payment_sentence(p: Dict[str, Any]) -> str:
    s = p["status"]
    amt = f"${p['amount_usd']:.2f}"
    if s == "SUCCESS":
        return f"Payment {p['payment_id']} of {amt} was confirmed by the payment provider (ref {p.get('provider_ref')})."
    if s == "FAILED":
        return f"Payment {p['payment_id']} of {amt} was declined by the provider, so you have not been charged and nothing was applied."
    if s == "PENDING":
        return f"Payment {p['payment_id']} of {amt} is still pending with the provider. I have not applied anything yet and will not until it is confirmed."
    return f"Payment {p['payment_id']} of {amt} has an unconfirmed outcome. I'm not going to say it worked until the provider confirms; a specialist will reconcile it."


billing_agent = BillingAgent()
