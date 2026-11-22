# Functional Specification Document (FSD)

Behaviour as implemented. Test references are in `tests/`. All back-end systems are sandbox adapters.

## 1. Conversation model

- A **session** belongs to exactly one subscriber. Using a session id with another subscriber raises a permission error (HTTP 403).
- Each turn: language detection → deterministic control flow (YES/NO, OTP code, consent reply) → intent classification and entity extraction → human-request check → routing to a specialist agent → tool calls through the gateway → response synthesis → guardrails → reply with sources and references.
- Short follow-ups are merged with the pending request (slot filling), e.g. an EID typed after "I want an eSIM".
- Language: English, Spanish, Hindi detected per message; short or numeric messages keep the conversation language. Fixed frame text (confirmation prompts, step-up, errors) is localised; the rest of the content is language-neutral facts from systems.

## 2. Authentication and assurance

| Level | How obtained | Allows |
|---|---|---|
| `unauthenticated` | none | Catalogue only |
| `basic_pin` | `POST /auth/login` with identifier and PIN (sandbox) | Reads, payments, plan/add-on change, complaints |
| `authenticated_mfa` | One-time code sent to the registered number, verified in conversation | Profile update, SIM/eSIM, porting, network soft reset |

Wrong code: 3 attempts, then step-up locks and the conversation is escalated. A code works once; a bare 6-digit message with no open challenge is answered "not waiting for a code". Elevation lasts 15 minutes by default.

## 3. Functional requirements

IDs are used in `docs/TRACEABILITY` form inside `IMPLEMENTATION_STATUS.md`.

### FR-1 Account and service (Customer Care Agent)
- FR-1.1 Show profile, registered services, current plan, service history.
- FR-1.2 Show prepaid balance, validity, data/voice/SMS remaining; postpaid usage summary.
- FR-1.3 Flag usage anomalies from OCS data when present.
- FR-1.4 Update contact preferences (confirmation) and profile fields (MFA + confirmation).

### FR-2 Complaints and cases
- FR-2.1 Capture the issue; ask for case-processing consent if not on file; remember the original request and resume it after consent.
- FR-2.2 Ask for confirmation, create the case, classify category and priority by policy, return reference and expected next step.
- FR-2.3 Suppress duplicates of the same open complaint within the dedup window and return the existing reference.
- FR-2.4 Return live status on "status of TCK-…".
- FR-2.5 Complex complaints are never auto-closed.

### FR-3 Billing (Billing Agent)
- FR-3.1 Retrieve the invoice, itemise charges, cite invoice line, CDR evidence and tariff KB reference for each explanation.
- FR-3.2 Register a dispute (consent + confirmation) with evidence; route to a billing specialist.
- FR-3.3 Any credit request is declined autonomously: the assistant says it cannot issue it, opens a specialist review, and offers handover.
- FR-3.4 Payment status inquiry and reconciliation of PENDING/UNKNOWN payments.

### FR-4 Recharge and bill payment
- FR-4.1 Recommend an eligible recharge pack; validate price and eligibility.
- FR-4.2 Ask for confirmation naming amount and method (last 4 only). Confirmation is single use, expires (5 minutes by default) and is cancelled by any new request from the customer.
- FR-4.3 Charge with an idempotency key. Duplicate submissions with the same key do not create a second charge.
- FR-4.4 Outcomes:

| Provider result | Customer message | System effect |
|---|---|---|
| SUCCESS | Applied, with reference | Recharge applied or invoice settled only now |
| FAILED/declined | Declined, nothing applied | None |
| PENDING | Not confirmed yet | Polled/reconciled; never reported as paid |
| Timeout (UNKNOWN) | Not confirmed; not charged again | One reconcile call; no blind retry |
| SUCCESS then OCS failure | Payment taken, top-up not applied, specialist notified, no second charge | Escalation |

### FR-5 Plans, add-ons, retention (Sales and Retention Agents)
- FR-5.1 List, compare and recommend plans from permitted usage context.
- FR-5.2 Check eligibility and price before any change; disclose grandfathered terms that would be lost.
- FR-5.3 Change plan or add-on after confirmation; read back the result from the system. A mismatch is reported as unverified and escalated.
- FR-5.4 Retention offers only within policy (`offer.apply`; value above 50 needs approval). Gap: marketing consent is recorded and opt-outs are honoured, but offers are not yet gated on a marketing-consent check.

### FR-6 Network (Network Agent)
- FR-6.1 Check known outages for the customer's sector; give verified cause and ETA only as returned by the system.
- FR-6.2 If an outage exists, create a P2 `service_down` ticket if needed and subscribe the customer to updates (consent-aware).
- FR-6.3 Otherwise run diagnostics, give guided steps, create a ticket and offer handover when unresolved.
- FR-6.4 Restoration notice is sent to subscribed, consenting customers when an operator posts a restore for the alarm.
- FR-6.5 No infrastructure change. Only a subscriber-level soft reset, with MFA and confirmation.

### FR-7 SIM, eSIM, number services (SIM & Device Agent)
- FR-7.1 Replacement, eSIM issue and porting: MFA step-up → resume request → confirmation → human approval → execution by an operator-only approve call. The customer is told approval is pending, never that it is done.
- FR-7.2 Device compatibility and activation guidance from catalogue/knowledge.

### FR-8 Orders (Order Agent)
- FR-8.1 Order, delivery, installation appointment and provisioning status from the order system.

### FR-9 Knowledge (Knowledge Agent)
- FR-9.1 Answer policy and how-to questions from approved documents (including FAQs on consent, outages, billing) with citations.
- FR-9.2 If retrieval relevance is below threshold or evidence is missing, say so and offer handover rather than guess.

### FR-10 Human handover
- FR-10.1 Triggers: customer asks for a human, low confidence, policy block, repeated failure, unavailable system, outcome unknown, rapid sentiment collapse (frustration velocity below threshold).
- FR-10.2 The manifest holds summary, intent, priority, suggested next action, transcript, tool results and actions attempted.
- FR-10.3 Operator claims, replies and resolves; the customer sees operator messages via polling; while handed over the bot does not answer.
- FR-10.4 After resolution the next customer message goes to the bot again.

### FR-11 Consent
- FR-11.1 Purposes: `case_processing` (default no), `marketing` (default no), `proactive_service_notices` (default yes). One-time codes and receipts are always sent.

### FR-12 Administration
- FR-12.1 Operator endpoints (API key outside development): approvals queue, handover queue, policy view and patch, agent permission set, audit query and chain verification, KPIs, outage restore.
- FR-12.2 Guard rails cannot be relaxed through the admin API: confirmation on payment, autonomous credit limit, human approval for SIM/eSIM/port, forbidden network change.

### FR-13 KPIs
- FR-13.1 Computed from event buffer, pending actions and conversations; reported as null with a note when the denominator is zero. See `docs/KPI_DEFINITIONS.md`.

### FR-14 Channels
- FR-14.1 Web chat page (`/customer`): sign-in, language selector, confirm/cancel buttons, step-up prompt, handover polling, CSAT.
- FR-14.2 WhatsApp webhook (signature check when a secret is configured), voice and contact-centre adapters normalise to the same pipeline. A phone number alone never grants MFA assurance.

## 4. Non-functional behaviour

- All customer-supplied text is rendered with `textContent` in the web client.
- Logs and audit masks card-like numbers, PINs and OTPs. The OTP value is never written to audit.
- Failures of a protected system open a circuit breaker (business refusals such as "invoice not found" do not).
- Each tool call has a timeout; a timeout on a state-changing call yields OUTCOME_UNKNOWN, not retry.

## 5. User-visible messages that must hold

1. Never "Done" for payment/recharge/plan/SIM until the system confirms (and for plan/add-on, until read-back matches).
2. Always include a reference (payment, case, handover, dispute) where one exists.
3. Always state when a human approval or handover is the next step.
