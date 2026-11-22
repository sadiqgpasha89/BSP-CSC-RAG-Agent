# Business Requirements Document (BRD)

| | |
|---|---|
| Product | Telecom Agentic AI Customer Experience Platform (repository `BSP-CSC-RAG-Agent`) |
| Version | 2.0 (draft for operator review) |
| Owner | Sadiq G Pasha |
| Status | Reflects what is implemented against **sandbox systems**. No live operator integration exists. |

## 1. Purpose

A telecom operator serving consumer mobile and broadband customers needs a chat assistant that completes verified customer tasks safely, not one that only answers FAQs. This document states the business need, scope, outcomes, constraints and acceptance, and maps each to the implementation in `IMPLEMENTATION_STATUS.md`.

## 2. Business problem

- Customer-care demand is 24/7 and spiky (outages, billing cycles), while human capacity is fixed and expensive.
- Many contacts are routine: balance, bill explanation, recharge, plan change, outage status, complaint status.
- Naive chatbots fail in three costly ways: they invent live facts (balances, outcomes), they act without proper authorisation or confirmation, and they trap customers away from humans.

## 3. Objectives and measures

| Objective | Measure (see `docs/KPI_DEFINITIONS.md`) |
|---|---|
| Verified self-service for routine tasks | Task completion rate (source-system verified), containment rate |
| Resolve on first contact | First-contact resolution |
| Lower cost and handling time | Cost per interaction, average handling time |
| Keep customers informed and satisfied | CSAT, repeat contact rate |
| Safe automation | API execution success, unsafe-action blocks, unsupported-answer rate |
| Transparent escalation | Escalation rate by reason |

Containment is never to be optimised alone. Targets are intentionally not set in this document: the plan requires targets to be agreed after baseline and load testing, which needs real traffic.

## 4. Stakeholders

| Stakeholder | Interest |
|---|---|
| Customers (consumer mobile and broadband) | Fast, correct, safe help in their language |
| Contact-centre operators and supervisors | Context-rich handovers, approval queue, fewer repeat questions |
| Billing, network and fraud teams | Evidence-backed disputes and tickets; no unsafe automation |
| Product and marketing | Plan discovery, eligible upsell with consent, retention offers within policy |
| Security, privacy, legal and compliance | Consent, audit, least privilege, jurisdiction-configurable policy |
| Platform and operations engineers | Observable, testable, replaceable components |

## 5. Scope

### 5.1 In scope (implemented on sandbox adapters)

1. Authentication, profile, account and service inquiry; complaints with consent, dedup, priority and status tracking.
2. Prepaid balance and usage; postpaid invoice retrieval; itemised charge explanation with evidence; dispute registration.
3. Recharge and bill payment with explicit confirmation, idempotency, and safe handling of declined, pending, unknown and partially failed outcomes.
4. Plan discovery, comparison, eligibility and price check, plan change, add-on change, retention offers within policy limits.
5. Mobile and broadband troubleshooting, outage check, technical tickets, consent-aware proactive outage notices.
6. SIM/eSIM assistance and number-portability initiation with step-up verification and human approval.
7. Order, appointment and service-request tracking.
8. Human handover with summary, priority, suggested next action, claim/reply relay.
9. Audit trail (tamper-evident), KPI service, operator console, policy and approval administration.
10. Channels: web chat page and channel-normalisation adapters for WhatsApp, voice and contact centre; English, Spanish and Hindi framing.

### 5.2 Planned, not implemented

Speech-to-text/text-to-speech, durable persistence, identity-provider integration, rate limiting at an API gateway, OpenTelemetry export, RAG delete/update API, knowledge-source admin UI, load and accessibility certification, automated complaint follow-up notifications, live LLM provider integration (an interface exists; default is a deterministic mock).

### 5.3 Out of scope / explicit exclusions (plan §12)

Autonomous change of live network infrastructure; unsupervised SIM swap, eSIM transfer or ownership change; unsupervised refunds or high-value credits; automatic closure of complex complaints; predictive maintenance and self-healing as production actions; complex B2B SLA and wholesale workflows; unrestricted data or system access by any LLM or agent. Phase 4 (enterprise-wide operations) is deferred.

## 6. Business rules (enforced outside the LLM)

| ID | Rule | Where enforced |
|---|---|---|
| BR-1 | Live balances, bills, payments, orders and network state come only from system APIs; the assistant never states a transaction succeeded before the authoritative system confirms it | Tool gateway, response synthesis, numeric-grounding guardrail |
| BR-2 | Consequential actions need explicit, single-use customer confirmation | `PendingAction` lifecycle |
| BR-3 | SIM replacement, eSIM issue and porting need MFA step-up and human approval | `config/policies.json`, gateway |
| BR-4 | Credits and refunds are never autonomous (`autonomous_limit` 0, hard ceiling 100) | Policy pack; guard rails that admins cannot relax |
| BR-5 | Payment: max 500 per payment, 1000 per day, 5 attempts per hour (sandbox defaults, configurable) | Policy pack |
| BR-6 | Complaints and disputes need consent for case processing (default: not granted); marketing consent default off; service notices on | Consent service |
| BR-7 | Agents have least-privilege access; unknown actions are denied | Policy pack `agent_permissions` |
| BR-8 | Human handover on customer request, low confidence, policy block, repeated failure, unavailable system | Supervisor |
| BR-9 | Legacy/grandfathered plan terms must be disclosed before a plan change | Sales agent |
| BR-10 | Complaint priority and SLA hours follow the configured rules (P1 4h, P2 24h, P3 72h, P4 120h defaults) | Policy pack, ticketing adapter |

## 7. Assumptions and dependencies

- Operator APIs are not available to this project; all enterprise systems are mocked with deterministic data and failure scenarios, and are labelled as such.
- Replacing a mock requires implementing the same adapter interface (`docs/TSD.md` §5); agent logic should not change.
- The operator supplies identity provider, payment service provider, durable storage, LLM/ASR provider choices, and deployment environment.

## 8. Items requiring operator, legal or regulatory review

These come from `compliance_review_items` in the policy pack and are not decided by this project:

1. Customer-data (CPNI-style) access rules and verification standards per jurisdiction.
2. Payment-card scope: confirm the PSP hosted-field/tokenisation model (the platform is designed never to see card data).
3. Consumer-protection rules on cancellation, retention offers and dispute-handling timelines.
4. Retention and deletion periods for conversations, audit and recordings; data residency of any LLM/ASR provider. (The policy pack carries default values of 30/730/365 days for conversation/audit/KPI data; the sandbox stores everything in memory, so these are configuration intent, not enforced retention.)
5. Regulatory complaint SLAs and mandatory complaint-number formats.
6. Number-portability and SIM-replacement identity-verification rules.

## 9. Risks

| Risk | Mitigation | Residual |
|---|---|---|
| Assistant states an unverified outcome | Outcome statuses, read-back, reconcile, tests | Depends on real adapters returning authoritative results |
| Prompt injection or social engineering | Control flow outside LLM, gateway authz, adversarial tests | A live LLM would need its own red-team evaluation |
| Mock behaviour differs from real systems | Contracts documented; replacement points explicit | Integration testing required per system |
| In-memory state lost on restart, no horizontal scale | Documented (ADR-009) | Must be replaced before production |
| KPI misreading (containment up, satisfaction down) | KPIs shown together; null when no data | Needs baseline from real traffic |

## 10. Acceptance

Acceptance criteria are the plan's §15. Status per criterion, with test references, is in `IMPLEMENTATION_STATUS.md` §8. The release is **not** declared production-ready: load, accessibility and deployment-reproducibility criteria are open.
