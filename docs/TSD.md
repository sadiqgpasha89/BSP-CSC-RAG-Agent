# Technical Specification Document (TSD)

How the system is built. Diagrams: `diagrams/README.md`. Decisions and trade-offs: `docs/SADR.md`.

## 1. Stack

Python 3.10+ (developed on 3.13), FastAPI + uvicorn, pydantic v2 and pydantic-settings, structlog, numpy/pandas/scikit-learn/networkx (retrieval and analytics), jinja2 (console pages), httpx (optional LLM provider). No database: state is in process memory. Frontend is plain HTML/CSS/JS served by FastAPI (`src/frontend`).

## 2. Repository layout

| Path | Purpose |
|---|---|
| `src/api/` | FastAPI app, routers (`chat`, `auth`, `channels`, `hitl`, `approvals`, `admin`, `metrics`, `mlops`, `network`, `rag`), operator auth dependency |
| `src/agents/` | `supervisor.py`, `base.py`, `entities.py`, eight specialist agents, shared state |
| `src/workflows/` | `control.py` (YES/NO, OTP, consent, resumption), `payments.py` |
| `src/tools/` | `gateway.py` (contracts + enforcement), sandbox adapters, circuit breaker |
| `src/core/` | `auth`, `policy`, `confirmations`, `consent`, `conversation`, `audit`, `i18n`, `proactive`, `config`, `security`, `logging`, `hybrid_router` |
| `src/rag/`, `src/data_engineering/` | BM25, vector, graph retrieval, ingestion/chunking, stream buffer, lakehouse sink |
| `src/datascience/` | intent classifier, language detection, sentiment, churn, guardrails |
| `src/hitl/` | handover manifest and queue, escalation manager |
| `src/channels/` | channel normalisation and WhatsApp signature check |
| `src/metrics/` | KPI service; legacy metric calculators (labelled illustrative) |
| `src/llm/` | provider interface and grounded rephrase |
| `config/policies.json` | policy pack |
| `data/` | sandbox subscribers, invoices, usage, payments, orders, plan catalogue, knowledge base |
| `tests/` | `unit`, `integration`, `security`, `edge_case`, `performance` |

## 3. Request pipeline

`POST /api/v1/chat/message` → `SupervisorAgent.process_turn` (runs in the FastAPI worker pool; the pipeline is synchronous).

1. Resolve session; refuse if it belongs to another subscriber.
2. Verify the signed session token to get the assurance level (`core/security.py`).
3. Detect language (`datascience/language.py`).
4. `ControlFlow.try_handle`: OTP code, consent reply, YES/NO on the latest pending action. These never reach an LLM.
5. Otherwise classify intent (lexical softmax over 9 classes) and extract entities.
6. Route to a specialist agent. Agents call tools only via `ToolGateway.invoke`.
7. Synthesis builds the message from tool results; advisory text goes to "notices" so numeric guardrails check only factual sentences.
8. Guardrails (grounding and faithfulness) validate; failure triggers handover.
9. Emit KPI events; return `SupervisorExecutionResponse` including pending action, step-up flag, sources, handover id.

## 4. Tool gateway (`src/tools/gateway.py`)

Order of checks for every call: contract exists → no other-customer identifiers in args → typed args (pydantic, `extra=forbid`) → auth context belongs to this subscriber and is unexpired and has the scope → policy engine decision → pending action frozen for consequential actions → execution with circuit breaker, timeout (`tool_timeout_sec` 5s), bounded retries (read-only only, `tool_max_retries` 2) and idempotency key → audit and KPI event.

`ToolStatus`: OK, DENIED, INVALID_ARGS, CONFIRMATION_REQUIRED, STEP_UP_REQUIRED, CONSENT_REQUIRED, APPROVAL_REQUIRED, ESCALATE, NOT_FOUND, FAILED, UNAVAILABLE, OUTCOME_UNKNOWN.

Verification: plan and add-on changes are read back from CRM; payments are only treated as SUCCESS when the provider says so; recharge/settlement tools require a verified payment id.

Tool inventory (name → policy action): `crm.get_profile/get_services/get_history`, `usage.get_balance/get_summary/get_anomalies`, `billing.get_invoice/explain_charges`, `catalog.*` (read), `network.check_outage/run_diagnostics`, `order.list/get/appointments`, `ticket.get/list` (read); `ticket.create`, `billing.register_dispute`, `payment.initiate`, `recharge.apply`, `invoice.settle`, `plan.change`, `addon.change`, `offer.apply`, `profile.update`, `preferences.update`, `sim.replace`, `esim.issue`, `port.initiate`, `network.soft_reset`, `notification.send` (write); `payment.get_status/reconcile`, `payment.list_methods` (read).

## 5. Adapter contracts and replacement points

Each adapter is a module-level object in `src/tools/` exposing methods called by the gateway's `ToolSpec`. To go live, implement the same method names and return shapes against the real system and change only the adapter module (and its entry in `ToolSpec` if a method moves).

| Domain | Sandbox adapter | Key methods | Behaviour in sandbox |
|---|---|---|---|
| CRM | `crm_adapter` | profile, services, history, change_plan, change_addon, update_profile | Seeded subscribers in `data/subscribers_catalog.json` |
| BSS/billing | `bss_adapter` | invoice, explain, dispute, settle | `data/sandbox/invoices.json`; credits human-approved only |
| OCS | `ocs_adapter` | balance, usage, anomalies, topup | `data/sandbox/usage.json`; `fail_topups` switch for tests |
| Catalogue | `catalog_adapter` | plans, compare, eligibility, recommend | `data/catalog/plans.json` |
| Payment | `payment_adapter` | initiate, status, reconcile | Methods `pm_ok`, `pm_decline`, `pm_pending`, `pm_timeout`; idempotent per reference |
| Ticketing | `ticketing_adapter` | create (dedup 10 min), get, list | Priority from policy rules |
| Orders | `order_adapter` | list, get, appointments | `data/sandbox/orders.json` |
| Notifications | `notification_adapter` | send, outbox | Outbox only; sandbox exposes OTP for testing; never in audit |
| OSS/network | `oss_adapter`, `provisioning_adapter` | alarms, diagnostics, soft reset, SIM/eSIM/port provisioning | Fixed sectors and alarms |

Real-system notes: expect different error taxonomies (map to `FAILED`/`UNAVAILABLE`/`OUTCOME_UNKNOWN`); payment adapters must supply a provider idempotency key and an authoritative status query; telecom-standard APIs (e.g. TM Forum Open APIs) are the preferred shape but their use is not assumed or implemented here.

## 6. Policy pack (`config/policies.json`)

`deny_unknown_actions: true`. Each action has `risk`, `min_auth`, and optionally `confirmation`, `step_up`, `human_approval`, `consent`, `max_amount`, `daily_limit`, `max_attempts_per_hour`, `forbidden`. `agent_permissions` is the least-privilege matrix (see `docs/SADR.md` ADR-004). The admin API may tighten but cannot relax guard rails (payment confirmation, autonomous credit limit, human approval for SIM/eSIM/porting, forbidden network change). Every change is audited with the actor.

## 7. State and data

All state is in process: conversations (LRU+TTL, subscriber-bound), pending actions, idempotency keys, consent, audit chain, handover queue, event buffer. See `diagrams/src/13-er-data-model.mmd`. A restart loses it. Multiple workers do not share it. This is a known limit (ADR-009).

## 8. Audit

`core/audit.py` keeps an append-only list where each record includes the previous record's hash; `verify_chain` detects edits. Secrets and card-like patterns are masked before write. This is tamper-evident, not immutable: production should ship records to write-once storage.

## 9. Knowledge and RAG

Sources: JSON documents in `data/knowledge_base` (tariffs, troubleshooting, policies, FAQs). `IngestionPipeline` chunks with metadata (doc id, version, effective dates, language, owner). `HybridRetriever` combines BM25, vector and graph scores with weights; relevance gate `tau_rag` stops weak evidence being used. Citations carry doc id and title. Update means re-running ingestion; there is no delete API.

## 10. Configuration

Environment via `.env` (see `.env.example`): `ENVIRONMENT`, `SECRET_KEY`, `ADMIN_API_KEY`, `CORS_ALLOWED_ORIGINS`, `DATA_DIR`, `LLM_PROVIDER`/`LLM_ENDPOINT`/`LLM_MODEL`/`LLM_API_KEY`, WhatsApp secrets, timeouts, TTLs, step-up limits. Outside development the app refuses to start without `SECRET_KEY` and `ADMIN_API_KEY`.

## 11. LLM layer

`src/llm`: `LLMProvider` interface, `MockProvider` (default, returns draft unchanged), `OpenAICompatibleProvider` (HTTP chat completions, untested against a live model). `grounded_rephrase` rejects any output that adds a number, identifier or URL not present in the draft, and falls back to the draft on error. It is not yet called from the supervisor; replies today are deterministic. Wiring it in is a small change but needs an evaluation set first.

## 12. Testing

`python -m pytest tests -q`. Categories: unit (gateway, adapters, i18n, LLM wrapper, KPI), integration (journeys, billing, handover, outage, provisioning), security/adversarial, edge cases, performance. `tests/conftest.py` resets all process-wide state before each test. API-route tests (`test_api_endpoints.py`, `test_api_hardening.py`) need FastAPI installed. Not present: browser end-to-end tests, load tests, labelled evaluation set.

## 13. Build, run, deploy

`make install`, `make run` (uvicorn on 8000), `make test`, `make diagrams`. `Dockerfile` (python:3.12-slim, non-root) and `.github/workflows/ci.yml` (tests + diagram parse check) are provided but have not been executed in the environment where this was authored. No Kubernetes manifests.
