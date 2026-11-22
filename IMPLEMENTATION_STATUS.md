# Implementation Status

Traceable backlog for every capability in the build plan (`BSP-CSC-RAG-plan.md`). Status values: **done** (implemented and covered by a passing automated test), **partial** (works, with a stated gap), **mock** (works against a labelled sandbox adapter only — no live operator system), **not started**, **deferred** (Phase 4 or an explicit plan exclusion).

Last verified: 148 tests collected; 147 pass in the non-API suite. The remaining failure (`test_module_loggers_are_lazy_so_configure_logging_applies`) was only seen with the stand-in web stack used where FastAPI is not installed; it is expected to pass in a normal environment but has **not** been confirmed. `tests/integration/test_api_endpoints.py` and `tests/integration/test_api_hardening.py` need FastAPI/httpx and **have not been run** in this verification, so the HTTP routes are compile-checked only.

"mock" means the adapter lives in `src/tools/*_adapter.py` / `data/sandbox/`, is deterministic, and is labelled as sandbox. No integration with a real operator system exists. Nothing in this repository claims otherwise.

## 1. Capabilities (plan §2)

| ID | Requirement | Status | Implementation | Tests | Gap / blocker |
|---|---|---|---|---|---|
| CAP-2.1.1 | Authentication and profile retrieval (MVP) | mock | `src/core/auth.py`, `src/api/routes/auth.py`, `crm_adapter` | `test_tool_gateway`, `test_adversarial` | Sandbox PIN login; real IdP (OIDC) is an external dependency |
| CAP-2.1.2 | Account, service, plan, history inquiry (MVP) | mock | `customer_care_agent`, `crm_adapter` | `test_journeys` | |
| CAP-2.1.3 | Complaint registration, reference, status, follow-up (MVP) | mock | `customer_care_agent`, `ticketing_adapter` (dedup, priority) | `test_journeys::complaint`, `test_adversarial::ticket_dedup` | Automated proactive follow-up on a case is not started (see CAP-P3.5) |
| CAP-2.1.4 | Profile/account updates with verification | mock | `profile.update` action (step-up + confirmation) | `test_tool_gateway` | |
| CAP-2.1.5 | Communication preferences and consent-aware notifications | mock | `core/consent.py`, `notification_adapter` | `test_tool_gateway`, `test_journeys` | |
| CAP-2.2.1 | Prepaid balance and usage inquiry (MVP) | mock | `ocs_adapter`, `customer_care_agent` | `test_journeys` | |
| CAP-2.2.2 | Postpaid bill/invoice retrieval (MVP) | mock | `bss_adapter` | `test_billing_workflow` | |
| CAP-2.2.3 | Itemised charge explanation with evidence (MVP) | mock | `billing_agent` (invoice + CDR + tariff KB refs) | `test_journeys::bill_explanation` | |
| CAP-2.2.4 | Payment/recharge status inquiry (MVP) | mock | `payment.read`, `payment.reconcile` | `test_journeys` | |
| CAP-2.2.5 | Recharge and bill payment with explicit confirmation (MVP) | mock | `workflows/payments.py`, `payment_adapter` (pm_ok/decline/pending/timeout) | `test_journeys::recharge_*`, `test_tool_gateway` | Real PSP and card tokenisation are external |
| CAP-2.2.6 | Failed-payment guidance, retry/status, reconciliation messaging | mock | `payment_sentence`, reconcile path | `test_journeys::payment_decline/timeout` | |
| CAP-2.2.7 | Billing dispute registration with evidence | mock | `billing.dispute`; credits human-approved only | `test_billing_workflow`, `test_adversarial` | |
| CAP-2.2.8 | Usage anomaly identification | partial | `usage.get_anomalies` (statistical, OCS data) | `test_tool_gateway` | Simple threshold method; no model evaluation |
| CAP-2.3.1 | Plan discovery, comparison, recommendation (MVP) | mock | `sales_agent`, `catalog_adapter` | `edge_case/test_legacy_plan_trap` | |
| CAP-2.3.2 | Eligibility and price checks (MVP) | mock | `catalog_adapter`, `plan.change` precheck | `test_journeys::plan_change` | |
| CAP-2.3.3 | Plan change and add-on (de)activation (MVP) | mock | `plan.change`, `addon.change` with read-back verification | `test_journeys`, `test_tool_gateway` | |
| CAP-2.3.4 | Data packs, roaming, VAS recommendations | partial | `sales_agent`, catalogue add-ons | `test_journeys` | Recommendation rules are static |
| CAP-2.3.5 | Personalised recommendations / upsell | partial | `sales_agent` uses permitted usage context | | Offers are not yet gated on a marketing-consent check (opt-outs are honoured); no A/B or uplift measurement |
| CAP-2.3.6 | Churn signals, retention, loyalty workflows | partial | `retention_agent`, `datascience/churn_predictor.py` | `test_regressions` | Offers capped by policy; churn model is not trained on real data |
| CAP-2.3.7 | Only policy-authorised offers executed | done | `offer.apply` in policy pack | `test_tool_gateway` | |
| CAP-2.4.1 | Mobile signal/call-drop/slow-data troubleshooting (MVP) | mock | `network_agent`, `oss_adapter` | `test_network_outage_workflow` | |
| CAP-2.4.2 | Broadband/router/Wi-Fi troubleshooting (MVP) | mock | `network_agent` | `test_network_outage_workflow` | |
| CAP-2.4.3 | Known outage check and verified restoration info (MVP) | mock | `network.read`, `oss_adapter` | `test_network_outage_workflow` | |
| CAP-2.4.4 | Guided diagnostics; technical tickets on failure | mock | `network_agent`, `ticket.create` | `test_network_outage_workflow` | |
| CAP-2.4.5 | Proactive outage notifications | mock | `core/proactive.py` (consent-aware) | `test_journeys::outage_*` | Restoration trigger is an admin call, not a live network feed |
| CAP-2.4.6 | Predictive fault / maintenance | deferred | | | Phase 4 |
| CAP-2.4.7 | No autonomous live-network change | done | `network.change` denied for every agent; `network.soft_reset` needs confirmation | `test_tool_gateway`, `test_adversarial` | |
| CAP-2.5.1 | SIM/eSIM assistance, activation, compatibility (MVP) | mock | `sim_device_agent`, `catalog_adapter` | `test_journeys::sim_*` | |
| CAP-2.5.2 | SIM replacement guidance and eligibility | mock | `sim.replace` | `test_journeys` | |
| CAP-2.5.3 | Number portability explanation and initiation | partial | `port.initiate` (human-approved) | `test_tool_gateway` | Initiation is queued for approval; no donor-operator exchange |
| CAP-2.5.4 | Device/service activation assistance | partial | `sim_device_agent` | | Guidance only |
| CAP-2.5.5 | Step-up for SIM swap/eSIM/recovery | done | OTP step-up in gateway + `workflows/control.py` | `test_adversarial`, `test_journeys` | |
| CAP-2.6.1 | Order tracking (MVP) | mock | `order_agent`, `order_adapter` | `test_provisioning_workflow` | |
| CAP-2.6.2 | Service request and appointment tracking (MVP) | mock | `order_agent` | `test_provisioning_workflow` | |
| CAP-2.6.3 | Workforce/field-service coordination | deferred | | | Phase 4 |
| CAP-2.7.1 | Human handover with context, summary, next action (MVP) | done | `hitl/warm_handoff.py` | `test_hitl_escalation`, `test_journeys::human_takeover` | |
| CAP-2.7.2 | Agent assist (suggested reply, KB retrieval, summary) | partial | summary + suggested action + priority in handoff manifest | `test_hitl_escalation` | No suggested-reply generation (needs LLM provider) |
| CAP-2.7.3 | Customer-visible indication of human takeover | done | handover state, claim/reply relay, `/customer` polling | `test_journeys::human_takeover` | |
| CAP-2.8.1 | Audit trails for conversations, tools, transactions (MVP) | done | `core/audit.py` hash chain | `test_adversarial::audit_*` | In-memory store; durable/WORM storage is a deployment item |
| CAP-2.8.2 | Operational dashboard (MVP) | partial | console tabs + `/admin/kpis` | `test_journeys::kpis_*` | Legacy "6-category" tab is illustrative demo data and labelled so |
| CAP-2.8.3 | Analytics (usage patterns, issues, churn trends) | partial | KPI service, lakehouse sink | | In-process only; no warehouse export |
| CAP-2.8.4 | Admin-configurable knowledge, permissions, policy | partial | `/admin/policy` (guard rails cannot be relaxed), audit query | `test_adversarial` | Knowledge-source admin UI/API not started |
| CAP-2.9.1 | Responsive web chatbot (MVP) | done | `src/frontend/templates/customer.html` | manual only | No browser automation test; accessibility not audited |
| CAP-2.9.2 | WhatsApp / voice / contact-centre adapters | partial | `src/channels/`, `/channels` routes (signature check) | `test_journeys` | Provider webhooks/approval not exercised |
| CAP-2.9.3 | Multilingual detection and responses | partial | `datascience/language.py`, `core/i18n.py` (en/es/hi) | `test_multilingual` | Fixed frame text and keyword intents; free-form quality depends on the LLM provider |
| CAP-2.9.4 | Voice STT/TTS | not started | `is_audio` flag and voice-friendly formatting only | `test_journeys::voice_*` | No STT/TTS provider wired |
| CAP-2.9.5 | Accessible interaction | partial | labelled form, live region, keyboard focus styles | | Not audited against WCAG |

## 2. Agents and orchestration (plan §4)

| ID | Requirement | Status | Implementation | Tests |
|---|---|---|---|---|
| AGT-1 | Orchestrator (routing, state, timeouts, composition) | done | `agents/supervisor.py`, `workflows/` | `test_journeys`, `test_agentic_loop` |
| AGT-2 | Customer Care Agent | done | `agents/customer_care_agent.py` | `test_journeys` |
| AGT-3 | Billing Agent | done | `agents/billing_agent.py` | `test_billing_workflow` |
| AGT-4 | Sales Agent | done | `agents/sales_agent.py` | `test_journeys` |
| AGT-5 | Network Agent | done | `agents/network_agent.py` | `test_network_outage_workflow` |
| AGT-6 | SIM & Device Agent | done | `agents/sim_device_agent.py` | `test_journeys` |
| AGT-7 | Order Agent | done | `agents/order_agent.py` | `test_provisioning_workflow` |
| AGT-8 | Retention Agent | done | `agents/retention_agent.py` | `test_regressions` |
| AGT-9 | Knowledge Agent with citations | done | `agents/knowledge_agent.py` | `test_journeys` |
| ORC-1 | Typed inputs/outputs and tool contracts | done | pydantic arg models, `extra=forbid` | `test_tool_gateway` |
| ORC-2 | Resumable workflows across turns | done | `conversation.notes`, step-up/consent resume | `test_journeys::resumption` |
| ORC-3 | Provenance of facts/results | done | sources on responses, `transaction_refs` | `test_journeys` |
| ORC-4 | Policy check before every sensitive tool call | done | `tools/gateway.py` + `core/policy.py` | `test_tool_gateway` |
| ORC-5 | Explicit confirmation for consequential actions | done | single-use pending actions | `test_tool_gateway`, `test_adversarial` |
| ORC-6 | Idempotency, duplicate prevention, bounded retries, timeouts, reconciliation, pending/unknown outcomes | done | gateway idempotency keys, breakers, `OUTCOME_UNKNOWN` | `test_tool_gateway`, `test_journeys` |
| ORC-7 | Bounded delegation / no circular hand-offs | done | fixed plan, step budget | `test_agentic_loop` |
| ORC-8 | Escalate on uncertainty, low confidence, outage, policy block, repeated failure, request | done | supervisor escalation rules | `test_hitl_escalation` |

## 3. Integrations (plan §6) — all sandbox adapters

CRM (`crm_adapter`), BSS/catalogue (`bss_adapter`, `catalog_adapter`), OSS/service inventory (`oss_adapter`, `provisioning_adapter`), billing (`bss_adapter`), OCS (`ocs_adapter`), network status (`oss_adapter`), payments (`payment_adapter`), identity (`core/auth.py`), ticketing (`ticketing_adapter`), orders (`order_adapter`), SIM/device inventory (`catalog_adapter`), notifications (`notification_adapter`), knowledge (`data/knowledge_base`, `rag/`), analytics (`metrics/kpi.py`, `lakehouse_sink`). **Status: mock.** Contracts and replacement points are described in `docs/TSD.md`.

## 4. AI / RAG (plan §7)

| ID | Requirement | Status | Notes |
|---|---|---|---|
| AI-1 | LLM for NLU/response | partial | `src/llm` provider interface; default `mock` returns deterministic drafts. `OpenAICompatibleProvider` exists but was never run against a live model |
| AI-2 | RAG with approved sources, chunking, metadata, citations | done | `data_engineering/ingestion_pipeline.py`, `rag/hybrid_retriever.py`; FAQ/policy documents added |
| AI-3 | RAG update and deletion pipeline | partial | re-ingest from files; no delete/update API |
| AI-4 | Typed tool calling | done | gateway |
| AI-5 | Conversation memory (short-term, subscriber-bound) | done | `core/conversation.py` |
| AI-6 | Intent classification | done | `datascience/intent_classifier.py` (9 classes, lexical) |
| AI-7 | Speech AI | not started | see CAP-2.9.4 |
| AI-8 | Guardrails: grounding, injection defence, permission checks, confirmation | done | guardrail validator + gateway; injection cases in `test_adversarial` |
| AI-9 | Evaluation harness | partial | tests + KPI proxies; no labelled evaluation set (see `docs/KPI_DEFINITIONS.md`) |
| AI-10 | Source-of-truth rule (APIs for live data; no LLM-made values) | done | messages built from tool results; numeric-grounding check |

## 5. Security and governance (plan §8)

| ID | Requirement | Status | Implementation / test |
|---|---|---|---|
| SEC-1 | Authentication before account data | done | session token + scopes; `test_adversarial` |
| SEC-2 | Customer-to-account ownership on every request | done | subscriber-bound sessions; cross-customer args rejected |
| SEC-3 | RBAC/ABAC, least privilege | done | `config/policies.json` agent→action matrix, deny by default |
| SEC-4 | Step-up verification | done | OTP; lockout after 3 bad codes |
| SEC-5 | Confirmation before consequential actions | done | |
| SEC-6 | Payment credentials not in prompts/logs/memory | done | only method ids and last-4; masking tested |
| SEC-7 | Encryption in transit/at rest, secrets management, rotation | not started | deployment responsibility; documented in `docs/TSD.md`; app requires `SECRET_KEY`/`ADMIN_API_KEY` outside development |
| SEC-8 | PII masking, minimisation, consent | done | audit masking, consent per purpose |
| SEC-9 | Prompt-injection / exfiltration / tool-abuse defence | done | `test_adversarial` |
| SEC-10 | Server-side validation of tool args | done | |
| SEC-11 | Policy engine decides; LLM proposes only | done | |
| SEC-12 | Human approval for sensitive/high-impact | done | operator-only approve endpoint |
| SEC-13 | Tamper-evident audit | done | hash chain verified; tamper test |
| SEC-14 | Jurisdiction-configurable compliance | partial | policies configurable; legal review items listed in `docs/BRD.md` |
| SEC-15 | Rate limiting at the gateway | not started | only OTP/payment-attempt limits exist |

## 6. Non-functional (plan §9)

Availability/scalability/HA: **not started** (single process; in-memory state — see `docs/SADR.md` ADR-009). Latency: measured in unit/perf tests only, no load test. Reliability: breakers, timeouts, idempotency **done** in the gateway. Observability: correlation ids on audit/KPI/events **done**; OpenTelemetry export **not started**. Accessibility, multilingual quality at scale: **partial**.

## 7. Delivery phases (plan §11) and exclusions (plan §12)

| Phase | Status |
|---|---|
| 0 Discovery/foundation | done for repo audit, architecture, threat model, contracts, data model, agent/tool matrix; CI workflow and Dockerfile added but not executed in this environment |
| 1 Customer-care MVP | done on sandbox adapters |
| 2 Transactional chatbot | done on sandbox adapters |
| 3 Proactive/personalised | partial: outage notices, retention, i18n, channel adapters done; usage insights simple; STT/TTS and complaint auto-follow-up not started |
| 4 Enterprise operations | deferred (extension points documented) |

Exclusions honoured: no autonomous network change; SIM swap/eSIM/porting need step-up + human approval; credits/refunds never autonomous (`autonomous_limit` 0); complex complaints are never auto-closed.

## 8. Deliverables (plan §14) and acceptance (plan §15)

| Deliverable | Status |
|---|---|
| 1–3 App, APIs, agents | done (sandbox) |
| 4 RAG pipeline | partial (no delete API) |
| 5 Tool contracts, adapters, mock docs | done (`docs/TSD.md`, `docs/API_CONTRACTS.md`) |
| 6 Auth, consent, policy, approval | done |
| 7 Five journeys + follow-ons | done (`tests/integration/test_journeys.py`) |
| 8 Handover and agent assist | done / assist partial |
| 9 Admin config | partial |
| 10 Dashboard, events, audit, tracing, alerts | partial (alerts and tracing export not started) |
| 11 Test suite | unit, integration, security/adversarial, multilingual done; contract and evaluation tests partial; browser E2E not started |
| 12 CI/CD, containers | CI workflow and Dockerfile added, not executed here; no Kubernetes manifests; rollback documented in `docs/RUNBOOK.md` |
| 13 Diagrams and docs | done (`diagrams/`, `docs/`) |
| 14 KPI definitions | done (`docs/KPI_DEFINITIONS.md`); baseline measurement needs real traffic |

Acceptance criteria not met yet: load/performance/accessibility targets (§15 bullet 11), reproducible deployment verification (bullet 12). All others are covered by tests as listed above, on sandbox systems.
