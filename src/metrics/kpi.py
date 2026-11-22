"""Plan §13 KPIs, computed from auditable events (tool outcomes, session telemetry, guardrail verdicts, conversation records).

Rules: a KPI with no denominator is reported as ``null`` with the reason, never a made-up default; every KPI names its
numerator, denominator, source and caveats. Containment is never reported alone: it sits beside verified task
completion, escalation reasons, CSAT and unsafe-action blocks.
"""

import statistics
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from src.core.confirmations import ActionStatus, pending_actions
from src.core.conversation import conversation_store
from src.data_engineering.stream_processor import stream_processor

REASON_BUCKETS = (
    ("identity_or_fraud", ("bypass", "social engineering", "verification", "locked")),
    ("policy_limit", ("credit", "refund", "limit", "approval", "only a human")),
    ("customer_request", ("human", "representative", "customer requested")),
    ("system_failure", ("unavailable", "unknown", "failures", "not applied", "circuit")),
    ("low_confidence", ("confidence", "relevance", "no grounded", "guardrail")),
    ("sentiment", ("frustration", "sentiment")),
)


def _bucket(reason: Optional[str]) -> str:
    low = (reason or "").lower()
    for name, keys in REASON_BUCKETS:
        if any(k in low for k in keys):
            return name
    return "other"


def _kpi(value: Optional[float], num: Optional[float], den: Optional[float], definition: str, source: str, note: str = "", unit: str = "pct") -> Dict[str, Any]:
    return {"value": None if value is None else round(value, 3), "numerator": num, "denominator": den, "unit": unit, "definition": definition, "source": source,
            "note": note or (None if den else "No data yet (denominator is zero).")}


def _ratio(num: float, den: float) -> Optional[float]:
    return (100.0 * num / den) if den else None


def _pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


class KPIService:
    def compute(self, since: float = 0.0) -> Dict[str, Any]:
        events = [e for e in list(stream_processor.message_buffer) if e.timestamp >= since]
        # One conversation = one session: fold the per-turn events by session id (a YES/OTP turn is not a new contact).
        folded: Dict[str, Dict[str, Any]] = {}
        for e in events:
            if e.event_type != "SESSION_COMPLETED":
                continue
            cur = folded.setdefault(e.session_id, {**e.payload, "duration_sec": 0.0, "cost_usd": 0.0, "escalated_to_human": False, "escalation_reason": None})
            cur["duration_sec"] += float(e.payload.get("duration_sec", 0.0))
            cur["cost_usd"] += float(e.payload.get("cost_usd", 0.0))
            if e.payload.get("escalated_to_human"):
                cur["escalated_to_human"], cur["escalation_reason"] = True, cur["escalation_reason"] or e.payload.get("escalation_reason")
        sessions = list(folded.values())
        tools = [e.payload for e in events if e.event_type == "TOOL_CALL"]
        unsafe = [e.payload for e in events if e.event_type == "UNSAFE_ACTION_BLOCKED"]
        guard = [e.payload for e in events if e.event_type == "GUARDRAIL_VERDICT"]

        n = len(sessions)
        contained = [s for s in sessions if not s.get("escalated_to_human")]
        by_sub = Counter(s.get("subscriber_hash") for s in sessions if s.get("subscriber_hash"))
        repeat_subs = sum(1 for c in by_sub.values() if c > 1)
        fcr_num = sum(1 for s in contained if by_sub.get(s.get("subscriber_hash"), 1) == 1)

        attempted = [t for t in tools if t["status"] in ("OK", "FAILED", "UNAVAILABLE", "OUTCOME_UNKNOWN")]
        ok = [t for t in attempted if t["status"] == "OK"]
        writes = [t for t in attempted if t.get("side_effect")]
        verified_writes = [t for t in writes if t["status"] == "OK" and t.get("verified", True)]

        pays = [t for t in tools if t["tool"] == "payment.initiate" and t["status"] in ("OK", "OUTCOME_UNKNOWN")]
        pay_status = Counter((t.get("payment_status") or "UNKNOWN") if t["status"] == "OK" else "UNKNOWN" for t in pays)

        durations = [float(s.get("duration_sec", 0.0)) * 1000.0 for s in sessions]
        csats = [c.csat for c in conversation_store.all() if c.csat is not None]
        all_actions = pending_actions.all()
        abandoned = [a for a in all_actions if a.status in (ActionStatus.EXPIRED, ActionStatus.CANCELLED, ActionStatus.PENDING_CONFIRMATION)]

        esc_reasons = Counter(_bucket(s.get("escalation_reason")) for s in sessions if s.get("escalated_to_human"))
        lang_sessions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for s in sessions:
            lang_sessions[s.get("language", "en")].append(s)
        total_cost = sum(float(s.get("cost_usd", 0.0)) for s in sessions)

        kpis = {
            "containment_rate": _kpi(_ratio(len(contained), n), len(contained), n, "Sessions that ended without human assistance / all sessions", "SESSION_COMPLETED",
                                     "Not a success measure on its own; read with task_completion_rate, csat and escalation_rate."),
            "first_contact_resolution": _kpi(_ratio(fcr_num, n), fcr_num, n, "Contained sessions whose customer did not contact us again in the window / all sessions", "SESSION_COMPLETED",
                                             "Approximation: a repeat session by the same customer within the window counts against FCR."),
            "task_completion_rate": _kpi(_ratio(len(verified_writes), len(writes)), len(verified_writes), len(writes),
                                         "State-changing operations that the source system confirmed (read-back where available) / state-changing operations attempted", "TOOL_CALL"),
            "average_handling_time_sec": _kpi(statistics.fmean(durations) / 1000.0 if durations else None, None, n, "Mean assistant turn duration", "SESSION_COMPLETED",
                                              "Per turn, not per interaction; human baseline must be supplied from the contact-centre system.", unit="sec"),
            "csat": _kpi(statistics.fmean(csats) if csats else None, sum(csats) if csats else None, len(csats), "Mean customer rating (1-5) submitted after the interaction", "conversation.csat",
                         unit="score_1_5"),
            "api_execution_success": _kpi(_ratio(len(ok), len(attempted)), len(ok), len(attempted), "Successful authorised enterprise operations / attempted operations (policy denials excluded)", "TOOL_CALL"),
            "escalation_rate": _kpi(_ratio(n - len(contained), n), n - len(contained), n, "Sessions transferred to human support / all sessions", "SESSION_COMPLETED"),
            "answer_accuracy_proxy": _kpi(_ratio(sum(1 for g in guard if g.get("passed")), len(guard)), sum(1 for g in guard if g.get("passed")), len(guard),
                                          "Draft answers that passed grounding/faithfulness guardrails / all validated drafts", "GUARDRAIL_VERDICT",
                                          "A PROXY. True accuracy needs a labelled evaluation set (see docs/KPI_DEFINITIONS.md)."),
            "payment_success": _kpi(_ratio(pay_status.get("SUCCESS", 0), len(pays)), pay_status.get("SUCCESS", 0), len(pays),
                                    "Payments confirmed SUCCESS by the provider / payments initiated (pending/failed/unknown reported separately)", "TOOL_CALL"),
            "cost_per_interaction_usd": _kpi(total_cost / n if n else None, total_cost, n, "Estimated inference cost / sessions", "SESSION_COMPLETED",
                                             "Inference cost only; excludes infrastructure and human cost.", unit="usd"),
            "latency_p50_ms": _kpi(_pct(durations, 0.5), None, n, "Median turn latency", "SESSION_COMPLETED", unit="ms"),
            "latency_p95_ms": _kpi(_pct(durations, 0.95), None, n, "95th percentile turn latency", "SESSION_COMPLETED", unit="ms"),
            "abandonment_rate": _kpi(_ratio(len(abandoned), len(all_actions)), len(abandoned), len(all_actions),
                                     "Confirmable actions the customer cancelled, let expire or left unanswered / all confirmable actions", "pending_actions"),
            "repeat_contact_rate": _kpi(_ratio(repeat_subs, len(by_sub)), repeat_subs, len(by_sub), "Customers with more than one session / customers", "SESSION_COMPLETED"),
            "unsafe_action_blocks": _kpi(float(len(unsafe)), len(unsafe), None, "Attempted actions blocked by policy (cross-customer, least privilege, forbidden)", "UNSAFE_ACTION_BLOCKED", unit="count"),
            "unsupported_answer_rate": _kpi(_ratio(sum(1 for g in guard if not g.get("passed")), len(guard)), sum(1 for g in guard if not g.get("passed")), len(guard),
                                            "Drafts blocked by guardrails / validated drafts", "GUARDRAIL_VERDICT"),
            "tool_failure_rate": _kpi(_ratio(len(attempted) - len(ok), len(attempted)), len(attempted) - len(ok), len(attempted), "Failed, unavailable or unknown tool outcomes / attempted", "TOOL_CALL"),
        }
        return {
            "window_events": len(events), "sessions": n, "kpis": kpis,
            "escalation_by_reason": dict(esc_reasons),
            "payment_outcomes": dict(pay_status),
            "by_language": {lang: {"sessions": len(v), "containment_pct": round(_ratio(sum(1 for s in v if not s.get("escalated_to_human")), len(v)) or 0.0, 2)} for lang, v in lang_sessions.items()},
            "guidance": "Never optimise containment alone: success = verified resolution + safe execution + customer satisfaction + transparent escalation.",
        }


kpi_service = KPIService()
