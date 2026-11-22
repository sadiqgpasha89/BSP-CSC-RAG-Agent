"""Shared agent plumbing: every agent reaches enterprise systems only through the tool gateway.

An agent's ``run`` returns an ``AgentOutcome``: system-authored message lines built from *tool data* (never free-form
model text), plus any pending action / step-up request that the supervisor must surface to the customer.
"""

import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.agents.state import AgenticSessionState
from src.tools.gateway import ToolContext, ToolResult, ToolStatus, tool_gateway

# Statuses that mean "the system is unhealthy / outcome unclear" and therefore count toward HITL failure escalation.
FAILURE_STATUSES = {ToolStatus.FAILED, ToolStatus.UNAVAILABLE, ToolStatus.OUTCOME_UNKNOWN}


# clarification kind -> entity keys that satisfy it (the next short reply is merged with the original request)
SLOT_KEYS = {"iccid": ["iccid"], "device_eid": ["device_eid"], "recharge_pack": ["pack_id", "amount"], "payment_id": ["payment_id"], "profile_value": ["email"]}


class AgentOutcome(BaseModel):
    agent: str
    messages: List[str] = Field(default_factory=list)       # evidence-derived statements (guardrail-checked)
    notices: List[str] = Field(default_factory=list)        # system-authored templates (no factual claims)
    status: str = "success"                                 # success | failed
    pending_action_id: Optional[str] = None
    pending_summary: Optional[str] = None
    needs_step_up: bool = False
    needs_login: bool = False
    clarification: Optional[str] = None                     # ask the customer for a missing detail
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    data: Dict[str, Any] = Field(default_factory=dict)


class BaseAgent:
    NAME = "BaseAgent"

    # ------------------------------------------------------------------ context
    def ctx(self, state: AgenticSessionState) -> ToolContext:
        return ToolContext(
            subscriber_id=state.subscriber_id, session_id=state.session_id, trace_id=state.trace_id, agent=self.NAME,
            auth_context=state.auth_context, step_up_verified=bool(state.blackboard.get("step_up_verified")),
            channel=state.blackboard.get("channel", "web"), language=state.blackboard.get("language", "en"),
        )

    # ------------------------------------------------------------------ tool call
    def call(self, state: AgenticSessionState, tool: str, args: Optional[Dict[str, Any]] = None, thought: Optional[str] = None) -> ToolResult:
        started = time.perf_counter()
        res = tool_gateway.invoke(self.ctx(state), tool, args or {})
        self._record(state, tool, args, res, (time.perf_counter() - started) * 1000.0, thought)
        return res

    def _record(self, state: AgenticSessionState, tool: str, args: Optional[Dict[str, Any]], res: ToolResult, latency_ms: float, thought: Optional[str]) -> None:
        failed = res.status in FAILURE_STATUSES
        output: Dict[str, Any] = {"success": not failed, "status": res.status.value, **({"error": res.message or res.error_code} if failed else {}), **res.data}
        state.record_step(agent_name=self.NAME, action=tool, tool_name=tool, tool_input=args or {}, tool_output=output,
                          thought=thought or f"{tool} -> {res.status.value}", latency_ms=latency_ms)
        if res.status == ToolStatus.ESCALATE:
            state.escalate(res.message or f"{tool} requires a human decision")
        if res.pending_action_id and res.status in (ToolStatus.CONFIRMATION_REQUIRED, ToolStatus.APPROVAL_REQUIRED):
            state.blackboard.setdefault("pending_actions", []).append(
                {"action_id": res.pending_action_id, "summary": res.pending_summary, "status": res.status.value, "tool": tool, "agent": self.NAME})
        if res.status in (ToolStatus.STEP_UP_REQUIRED, ToolStatus.CONSENT_REQUIRED):
            state.blackboard["resume_request"] = {"agent": self.NAME, "tool": tool, "args": args or {}}

    # ------------------------------------------------------------------ outcome helpers
    def outcome(self, **kw: Any) -> AgentOutcome:
        return AgentOutcome(agent=self.NAME, **kw)

    def finish(self, state: AgenticSessionState, out: AgentOutcome) -> AgentOutcome:
        """Fold any gateway side-channel signals (pending action, step-up) into the outcome."""
        pend = state.blackboard.get("pending_actions") or []
        mine = [p for p in pend if p.get("agent") == self.NAME]
        if mine and not out.pending_action_id:
            out.pending_action_id, out.pending_summary = mine[-1]["action_id"], mine[-1]["summary"]
        if not any(st.agent_name == self.NAME for st in state.execution_stack):
            state.record_step(agent_name=self.NAME, action="RESPOND", thought="Answered without calling a system (clarification or policy notice).")
        state.blackboard.setdefault("agent_outcomes", {})[self.NAME] = out.model_dump()
        if out.clarification in SLOT_KEYS:
            state.blackboard["awaiting_slot"] = {"keys": SLOT_KEYS[out.clarification], "agent": self.NAME}
        if out.status == "failed":
            state.blackboard.setdefault("failed_agents", []).append(self.NAME)
        return out

    @staticmethod
    def interpret(res: ToolResult, out: AgentOutcome, *, unavailable: str = "That system is not responding right now, so I could not complete this.") -> bool:
        """Translate non-OK gateway statuses into outcome flags. Returns True when the caller may use ``res.data``."""
        s = res.status
        if s == ToolStatus.OK:
            return True
        if s == ToolStatus.STEP_UP_REQUIRED:
            out.needs_step_up = True
        elif s in (ToolStatus.CONFIRMATION_REQUIRED, ToolStatus.APPROVAL_REQUIRED):
            out.pending_action_id, out.pending_summary = res.pending_action_id, res.pending_summary
        elif s == ToolStatus.DENIED:
            if "below the required" in (res.message or "").lower() or "not authenticated" in (res.message or "").lower():
                out.needs_login = True
            else:
                out.notices.append("I'm not able to do that on this account. A specialist can look at it with you.")
        elif s == ToolStatus.CONSENT_REQUIRED:
            out.clarification = "consent"
            out.notices.append("To proceed I need your permission to process this request. Reply \"I agree\" to allow it.")
        elif s in (ToolStatus.NOT_FOUND, ToolStatus.INVALID_ARGS):
            out.notices.append(res.message or "I could not find that.")
        elif s == ToolStatus.ESCALATE:
            out.notices.append("This needs a specialist's decision, so I have passed it to one.")
        elif s == ToolStatus.OUTCOME_UNKNOWN:
            out.notices.append("The provider did not confirm the result yet, so I am not going to tell you it worked. I'll check its status.")
            out.status = "failed"
        else:
            out.notices.append(unavailable)
            out.status = "failed"
        return False
