# Software Architecture Decision Records (SADR)

Format: context, decision, consequences. Status "accepted" means implemented.

## ADR-001 Single enforcement point for every enterprise action (accepted)
**Context.** The plan requires policy, identity and confirmation checks before every sensitive call and says model output is never authorisation. Earlier code let agents call adapters directly.
**Decision.** Agents may only call `ToolGateway.invoke`. The gateway validates arguments, binds the call to the authenticated subscriber, evaluates policy, freezes consequential calls as pending actions and audits everything.
**Consequences.** One place to test and review. Adding a tool means adding a `ToolSpec` and policy action; an agent calling an adapter directly is a defect. Small latency overhead.

## ADR-002 Deterministic control flow outside the LLM (accepted)
**Context.** "Yes", OTP codes and consent replies decide whether money moves or identity is elevated.
**Decision.** `workflows/control.py` handles them by rule, before intent classification. Confirmations are single use, bound to a hash of tool and arguments, expire, and are cancelled by any new request.
**Consequences.** A late or replayed "yes" cannot resurrect an action. Natural-language variants must be listed (en/es/hi sets); unknown phrasings fall through to normal routing.

## ADR-003 Messages from tool results, language model optional (accepted)
**Context.** Live values must never come from a model (plan §7).
**Decision.** Agents build the factual message from structured tool results. Advisory text is separate "notices". A guardrail checks numbers in the factual part against evidence. An LLM, when wired, may only rephrase via `grounded_rephrase`, which discards output with new numbers/IDs/URLs.
**Consequences.** Replies are accurate but plain. Free-form quality and multilingual fluency need the LLM layer, which is not yet connected and not evaluated.

## ADR-004 Deny-by-default policy pack with least-privilege agent matrix (accepted)
**Decision.** `config/policies.json` lists actions with risk, minimum assurance, limits and required controls; each agent has an explicit action list; unknown actions are denied. Guard rails cannot be loosened through the admin API.
**Consequences.** Policy is data, reviewable by compliance, changeable without code. A misconfigured permissions list fails closed.

## ADR-005 Human approval for the highest-risk actions (accepted)
**Context.** Plan §12 excludes unsupervised SIM swap, eSIM transfer, porting, and high-value credits.
**Decision.** These end in `AWAITING_APPROVAL`; only an operator-authenticated `approve` executes them. Credits have an autonomous limit of 0.
**Consequences.** The assistant says "approval pending", never "done". Operator throughput becomes a dependency.

## ADR-006 Payment truth comes from the provider; unknown is a first-class state (accepted)
**Decision.** Payment states PENDING, SUCCESS, FAILED, UNKNOWN. Recharge and invoice settlement tools require a verified SUCCESS. Timeouts become `OUTCOME_UNKNOWN`, trigger a reconcile call, and are never retried blindly. Idempotency keys prevent duplicate charges.
**Consequences.** A customer may wait for reconciliation. A payment success followed by an OCS failure escalates and states plainly that the customer will not be charged again.

## ADR-007 Hash-chained audit log (accepted)
**Decision.** Each record stores the hash of the previous one; verification is exposed to operators; secrets are masked before write.
**Consequences.** Tampering with in-memory history is detectable. It does not stop a privileged process from rewriting the whole chain: external write-once storage is needed in production.

## ADR-008 Sandbox adapters behind stable interfaces, labelled as mock (accepted)
**Decision.** Every enterprise system is a deterministic adapter with seeded data and switchable failure modes. Docs and UI say "sandbox".
**Consequences.** Journeys and failure paths are testable offline. Passing tests prove orchestration and safety logic, not integration with real systems.

## ADR-009 In-process state for the reference implementation (accepted, with known limit)
**Context.** The goal here is correctness of agent logic and safety controls; a database would add moving parts without changing those.
**Decision.** Conversations, pending actions, consent, audit and queues are held in memory with TTL/LRU limits.
**Consequences.** Not horizontally scalable, loses state on restart, not durable for audit. Before production, replace stores behind their existing class interfaces with Redis/Postgres and external audit storage.

## ADR-010 Multi-agent with a single orchestrator, direct response for simple cases (accepted)
**Decision.** A supervisor routes each request to one specialist agent (more only when the intent spans domains), with a step budget and no agent-to-agent delegation loop. Eight specialists mirror the plan's list.
**Consequences.** Simple, auditable flows and no circular delegation. Multi-domain requests are handled sequentially, not in parallel.

## ADR-011 Consent per purpose, defaults conservative (accepted)
**Decision.** Purposes `case_processing`, `marketing`, `proactive_service_notices`. Case processing needs an explicit "I agree" and the original request resumes afterwards. Operational messages (OTP, receipts) are not consent-gated.
**Consequences.** One extra turn for first-time complaints. Gap: marketing consent is not yet checked before offers.

## ADR-012 Handover is a conversation state, not just a flag (accepted)
**Decision.** While a handover is PENDING or ACTIVE, customer messages are relayed to the operator and the bot does not answer; resolution returns control to the bot.
**Consequences.** No bot/human cross-talk. Customer client must poll for operator replies (WebSocket push not implemented).

## ADR-013 Provider-agnostic LLM interface with grounding wrapper (accepted, not wired)
**Decision.** `LLMProvider` ABC with mock default; OpenAI-compatible adapter for a self-hosted endpoint. Vendor choice (Gemini, GPT, Claude) is left to the operator.
**Consequences.** No vendor lock-in; no live-LLM behaviour has been tested here.

## ADR-014 Repository cleanup decisions (accepted)
Removed `alignment_pipeline` (training-time stub unrelated to customer journeys) and the old `provisioning_agent` (superseded by SIM & Device and Order agents). Moved the legacy architecture spec and the engineering article to `docs/archive/`. Replaced fabricated CSAT/NPS/churn-equity constants with measured-or-null values and labelled the legacy metrics tab as illustrative.
