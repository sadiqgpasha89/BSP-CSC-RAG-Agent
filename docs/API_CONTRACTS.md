# API Contracts

Base path `/api/v1`. Interactive schema is served by FastAPI at `/docs` and `/openapi.json` when the app is running. This page summarises intent, auth and semantics. **Operator** endpoints require the `X-API-Key` header outside development (`ADMIN_API_KEY`). **Customer** endpoints require a `session_token` from `/auth/login` outside development. Everything is backed by sandbox adapters.

## Customer

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/login` | none | Body `{identifier, pin}` (sandbox PIN). Returns `{success, subscriber_id, session_token, auth_level: basic_pin}`. 401 on failure. |
| POST | `/chat/message` | token | Body `{subscriber_id, message, session_id?, session_token?, channel?, language_hint?, is_audio?, historical_sentiments?}`. Returns the turn result (below). 404 unknown subscriber, 403 session of another customer. |
| POST | `/chat/confirm` | token | Body `{subscriber_id, session_id, session_token?}`. Same as the customer replying YES to the latest pending action. |
| POST | `/chat/cancel` | token | Same as NO. |
| POST | `/chat/csat` | token | Body `{subscriber_id, session_id, rating 1-5, resolved?}`. Stores the rating on the conversation. |
| GET | `/chat/{session_id}/messages?subscriber_id=&after=&session_token=` | token | Operator replies since timestamp `after`, plus `handover_state`. 404 if the session is not the caller's. |

### Turn result (`SupervisorExecutionResponse`)

`trace_id`, `session_id`, `subscriber_id`, `final_response`, `intent_detected`, `is_multi_intent`, `sentiment_label`, `sentiment_score`, `churn_risk`, `execution_plan[]`, `steps_executed`, `retrieved_chunks_count`, `guardrail_passed`, `escalated_to_human`, `escalation_reason?`, `cloud_routing`, `latency_ms`, `language`, `pending_action?` `{action_id, summary, status, expires_at}`, `requires_step_up`, `sources[]`, `handover_id?`, `agents_involved[]`.

Client rules: show `pending_action.summary` with Confirm/Cancel; show a code input when `requires_step_up`; poll `/chat/{id}/messages` when `handover_id` is set; never display a transaction as complete unless `final_response` says so.

## Channels

| Method | Path | Notes |
|---|---|---|
| GET | `/channels/whatsapp` | Meta webhook verification (`hub.mode`, `hub.verify_token`, `hub.challenge`); compares to `WHATSAPP_VERIFY_TOKEN`. |
| POST | `/channels/whatsapp` | Verifies `X-Hub-Signature-256` with `WHATSAPP_APP_SECRET`; outside development a missing secret refuses all traffic. Returns replies per message. The sender's number maps to a subscriber but only gives `basic_pin`-level trust; step-up still applies. |
| POST | `/channels/voice` | Body: `InboundMessage` (`channel, subscriber_id, session_id, text, is_audio, language_hint, session_token`). Text is assumed to come from an external STT; reply text is short and markup-free for TTS. |
| POST | `/channels/contact-centre` | Same shape; for agent-assisted or IVR-fed sessions. |

`OutboundMessage`: `channel, session_id, text, buttons[], pending_action_id?, handover_id?, escalated, language, meta{trace_id, requires_step_up}`.

## Operator (X-API-Key)

| Method | Path | Purpose |
|---|---|---|
| GET | `/hitl/queue` | Open handovers (PENDING/ACTIVE) ordered by priority, with summary and suggested action. |
| POST | `/hitl/claim` | `{escalation_id, operator}` → ACTIVE. Only the claiming operator may reply. |
| POST | `/hitl/reply` | `{escalation_id, operator, text}` → message visible to the customer. |
| POST | `/hitl/resolve` | `{escalation_id, resolution_notes}` → RESOLVED_BY_HUMAN; bot resumes. |
| GET | `/approvals` | Actions in AWAITING_APPROVAL. |
| POST | `/approvals/{action_id}/approve` | `{operator, note?}` executes through the gateway and audits. The approver identity is recorded. |
| POST | `/approvals/{action_id}/reject` | `{operator, note?}`. |
| GET | `/admin/policy` | Policy pack snapshot and version. |
| PATCH | `/admin/policy/actions/{action}` | `{actor, patch}`; 403 if it would relax a guard rail. Audited. |
| PUT | `/admin/policy/agents/{agent}` | `{actor, actions[]}`; 403 for forbidden grants. Audited. |
| GET | `/admin/audit` | Filter by `correlation_id`, `session_id`, `subscriber_id`, `event_type`, `limit`. |
| GET | `/admin/audit/verify` | Hash-chain verification `{valid, ...}`. |
| GET | `/admin/kpis` | Plan §13 KPIs with numerator, denominator, definition, caveat; null when no data. |
| GET | `/admin/i18n/coverage` | Message-catalogue coverage per language. |
| POST | `/admin/outage/restore` | `{alarm_id}` → notify subscribed customers (consent-aware). Returns count. |
| GET | `/chat/subscribers` | Demo subscribers list. |

## Platform (pre-existing, retained)

`GET /health`; `/metrics/summary` (legacy illustrative metrics), `/metrics/cost-arbitrage`; `/rag/search`, `/rag/subgraph/{entity_id}`; `/network/topology`, `/network/alarms`, `/network/otdr-test` and `/network/core-reset` (operator, sandbox); `/mlops/drift`, `/mlops/models`, `/mlops/eval-sample`, `/mlops/self-heal` (operator).

## Error conventions

400/422 validation; 401 missing/invalid credentials; 403 forbidden or cross-customer; 404 not found (also used to avoid revealing other customers' sessions); 500 generic message only, details in server logs. Business refusals (declined payment, ineligible plan) are normal 200 responses with explanatory text, not HTTP errors.

## Tool contracts (internal)

Typed pydantic argument models in `src/tools/gateway.py` (`extra=forbid`); each `ToolSpec` names the tool, policy action, target system and argument model. See `docs/TSD.md` §4–5 for the inventory and adapter replacement points.
