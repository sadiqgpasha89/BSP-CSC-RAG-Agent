"""Session State, Entity Blackboard & Multi-Agent Execution Stack."""

import time
from typing import Dict, List, Any, Optional, Set
from pydantic import BaseModel, Field
from src.core.security import AuthContext


class ExecutionStep(BaseModel):
    step_index: int
    agent_name: str
    action: str
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: Optional[Dict[str, Any]] = None
    thought: Optional[str] = None
    latency_ms: float = 0.0
    timestamp: float = Field(default_factory=time.time)


class AgenticSessionState(BaseModel):
    """Global conversational blackboard and execution memory shared across all agents."""
    session_id: str
    trace_id: str
    subscriber_id: str
    auth_context: Optional[AuthContext] = None

    # Working Memory / Blackboard
    blackboard: Dict[str, Any] = Field(default_factory=dict)

    # Ingress Triage Context
    raw_utterance: str = ""
    sanitized_utterance: str = ""
    pii_token_map: Dict[str, str] = Field(default_factory=dict)
    primary_intent: str = "GENERAL_INQUIRY"
    secondary_intents: List[str] = Field(default_factory=list)
    sentiment_score: float = 0.0
    sentiment_label: str = "NEUTRAL"
    frustration_velocity: float = 0.0
    churn_risk_score: float = 0.10

    # Multi-turn Trajectory Stack & Loop Detection
    execution_stack: List[ExecutionStep] = Field(default_factory=list)
    agent_delegation_history: List[str] = Field(default_factory=list)
    step_count: int = 0
    consecutive_tool_failures: int = 0
    invocation_signatures: List[str] = Field(default_factory=list)

    # Retrievial and Guardrail Artifacts
    retrieved_chunks: List[Dict[str, Any]] = Field(default_factory=list)
    guardrail_passed: bool = True
    guardrail_violations: List[str] = Field(default_factory=list)

    # Terminal Status
    is_completed: bool = False
    is_escalated_to_human: bool = False
    escalation_reason: Optional[str] = None
    final_synthesized_response: Optional[str] = None

    def record_step(
        self,
        agent_name: str,
        action: str,
        tool_name: Optional[str] = None,
        tool_input: Optional[Dict[str, Any]] = None,
        tool_output: Optional[Dict[str, Any]] = None,
        thought: Optional[str] = None,
        latency_ms: float = 0.0,
    ) -> ExecutionStep:
        """Append an atomic reasoning or action step to the execution trajectory."""
        self.step_count += 1
        self.agent_delegation_history.append(agent_name)

        step = ExecutionStep(
            step_index=self.step_count,
            agent_name=agent_name,
            action=action,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            thought=thought,
            latency_ms=latency_ms,
        )
        self.execution_stack.append(step)

        # Track consecutive failures for HITL escalation. Agents expose an explicit top-level ``success`` flag /
        # ``error`` key; previously only these top-level keys were inspected while agents nested backend errors
        # one level down, so the "two consecutive tool failures" trigger could never fire.
        if tool_output is not None and (tool_output.get("success") is False or "error" in tool_output):
            self.consecutive_tool_failures += 1
        elif tool_name is not None:
            self.consecutive_tool_failures = 0

        return step

    def escalate(self, reason: str) -> None:
        """Flag the session for human handoff, accumulating (not overwriting) the reasons."""
        self.is_escalated_to_human = True
        existing = [r for r in (self.escalation_reason or "").split(" | ") if r]
        if reason not in existing:
            existing.append(reason)
        self.escalation_reason = " | ".join(existing)

    def register_invocation(self, agent_name: str, instruction: str) -> bool:
        """Record a delegation; returns True if the same agent was already invoked with the identical sub-goal.

        Implements the spec rule (Section 10.4): the same sub-agent invoked twice with identical sub-goals is a loop.
        """
        signature = f"{agent_name}::{instruction}"
        repeated = signature in self.invocation_signatures
        self.invocation_signatures.append(signature)
        return repeated

    def detect_cyclic_loop(self) -> bool:
        """Check if an infinite delegation loop is occurring among sub-agents."""
        if len(self.agent_delegation_history) >= 4:
            last_4 = self.agent_delegation_history[-4:]
            # Pattern like [A, B, A, B]
            if last_4[0] == last_4[2] and last_4[1] == last_4[3] and last_4[0] != last_4[1]:
                return True
        return False
