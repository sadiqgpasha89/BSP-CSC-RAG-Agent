# Threat Model

Method: STRIDE over the data-flow diagram in `diagrams/src/18-threat-model-dfd.mmd`. Scope is the platform as implemented with sandbox back ends. Status: **mitigated** (control exists and is tested), **partial**, or **open**.

## Assets
Customer CPNI (usage, bills, services), payment instruments (method ids only), account control (SIM, number), money movement, audit integrity, operator privileges, policy pack.

## Trust boundaries
Internet ↔ API; agent/LLM decisions (untrusted) ↔ tool gateway (trusted enforcement); operator console ↔ gateway; platform ↔ enterprise systems; knowledge base content (untrusted text) ↔ agents.

## Threats

| ID | Threat | STRIDE | Mitigation (code) | Test | Status |
|---|---|---|---|---|---|
| T1 | Session takeover: another customer uses a known session id | S, I | Sessions are bound to the subscriber; mismatch raises PermissionError (403); message polling returns 404 for foreign sessions | `test_session_cannot_be_taken_over_by_another_subscriber` | mitigated |
| T2 | Prompt injection to obtain other customers' data, credits or policy changes | T, E | Decisions and permissions are outside the model; gateway rejects other-customer identifiers in args; credits have autonomous limit 0; policy edits are operator-only | `test_prompt_injection_*` (4 attack strings) | mitigated for the deterministic pipeline; a live LLM needs its own red-team |
| T3 | Forged channel webhook | S | `X-Hub-Signature-256` HMAC; refused without a secret outside development; phone number gives only basic assurance | `test_whatsapp_signature_*`, `test_whatsapp_number_alone_*` | mitigated |
| T4 | Over-privileged tool call by an agent | E | Deny-by-default policy; per-agent action matrix; admin cannot grant forbidden actions | `test_policy_guard_rails_*`, gateway tests | mitigated |
| T5 | Poisoned or malicious knowledge document | T | Only approved ingested sources; retrieval relevance gate; answers cite sources; knowledge text is never executed or used as authorisation | retrieval/guardrail tests | partial (no content-signing of documents; no scan for injected instructions) |
| T6 | Cross-customer arguments in tool calls | I, E | `subscriber_id`/`msisdn` in args refused; context bound to subscriber; confirmation ids from another customer return NOT_FOUND | `test_confirmation_ids_from_another_customer_*`, gateway tests | mitigated |
| T7 | Replay or late confirmation ("yes" twice) / duplicate payment | T | Single-use confirmation, TTL, cancelled by new requests; idempotency keys; one charge per reference | `test_new_request_cancels_*`, `test_yes_without_pending_action_*`, journeys | mitigated |
| T8 | Admin relaxes controls | E | Guard rails cannot be loosened via API; changes audited with actor | `test_policy_guard_rails_*` | mitigated; operator authentication itself is a shared API key (partial) |
| T9 | Operator self-approves own request / approval without customer confirmation | E, R | Approvals only after customer confirmation; approver recorded in audit; operator-only endpoint | gateway/provisioning tests | partial: no separation of duties between claiming and approving operators |
| T10 | Audit tampering | T, R | Hash chain with verification endpoint | `test_audit_log_is_tamper_evident_*` | partial: in-memory, a privileged process can rewrite the whole chain |
| T11 | OTP brute force or reuse | S | 3 attempts then lock + escalation; code single use; bare numeric messages without a challenge are ignored; OTP never in audit | `test_otp_brute_force_*`, `test_otp_is_not_reusable_*` | mitigated |
| T12 | Privilege escalation by claiming to be verified | E | Assurance comes only from signed token and verified step-up, never from message text | `test_unverified_session_cannot_be_elevated_*` | mitigated |
| T13 | Sensitive data leakage in logs/prompts | I | Masking of card-like numbers, PINs, OTPs before audit/log | `test_audit_log_is_tamper_evident_and_masks_secrets` | mitigated for known patterns; not a general PII detector |
| T14 | Complaint/notification spam and ticket flooding | D | Ticket dedup window; notification consent; payment attempts per hour limit | `test_ticket_dedup_*` | partial: no API-level rate limiting |
| T15 | Dependency outage causing wrong answers | D, T | Circuit breakers, timeouts, UNAVAILABLE/OUTCOME_UNKNOWN statuses, escalation | gateway tests, journeys | mitigated |
| T16 | Cross-site scripting through chat text | T | Web client uses `textContent`; no HTML rendering of messages | manual review | mitigated in the provided client; other clients must do the same |
| T17 | Secrets in repository or weak default keys | I | `.env.example` only; app refuses to start outside development without `SECRET_KEY` and `ADMIN_API_KEY` | settings validation tests | mitigated; sandbox PINs are demo-only and must not be used beyond development |
| T18 | Transport and at-rest protection | I | Not provided by the app | n/a | open: TLS termination, disk encryption and key rotation are deployment responsibilities |

## Residual risk summary

Open or partial: operator identity (single API key), separation of duties, durable tamper-evident audit, rate limiting, document provenance, encryption and key management, LLM-specific attacks once a model is connected. These are listed in `IMPLEMENTATION_STATUS.md` and must be closed before production.
