# Runbook

Operational procedures for the reference implementation. It runs one process with in-memory state; several steps below say what a durable deployment would need to add. Nothing here was exercised against a production environment.

## 1. Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # ENVIRONMENT=development works without keys
make run                      # http://localhost:8000  (operator console)  /customer (customer chat)
make test
```

Demo logins: identifier `SUB-882194`, `SUB-556201`, `SUB-102948`, `SUB-449182` or `SUB-773019`, sandbox PIN shown on the page. Payment method ids for failure scenarios: `pm_ok`, `pm_decline`, `pm_pending`, `pm_timeout`.

## 2. Configure for a non-development environment

1. Set `ENVIRONMENT=staging|production`, a random `SECRET_KEY` (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) and `ADMIN_API_KEY`. The app will not start otherwise.
2. Set `CORS_ALLOWED_ORIGINS` to the real origins. Put TLS and an API gateway/WAF in front.
3. Set `WHATSAPP_APP_SECRET` and `WHATSAPP_VERIFY_TOKEN` if the WhatsApp channel is used; without the secret the webhook refuses traffic outside development.
4. Replace the sandbox PIN login with the operator's identity provider before any real customer uses it.
5. Container: `docker build -t bsp-csc .` then `docker run -p 8000:8000 --env-file .env bsp-csc` (Dockerfile provided, not tested here). Cloud deployment: see `docs/DEPLOYMENT.md`; run a single instance while state is in memory.

## 3. Health and monitoring

- `GET /api/v1/health`.
- `GET /api/v1/admin/kpis`: watch `tool_failure_rate`, `unsafe_action_blocks`, `unsupported_answer_rate`, `payment_success` and its pending/unknown counts, escalation reasons.
- `GET /api/v1/admin/audit/verify` should report `valid: true`. If not, treat it as a security incident.
- Logs are structured (structlog) and carry trace ids; use the `trace_id` from a turn result to find audit records via `/admin/audit?correlation_id=`.
- Not yet provided: OpenTelemetry export, alerting rules.

## 4. Routine operator tasks

| Task | How |
|---|---|
| Work the handover queue | `GET /hitl/queue` → `POST /hitl/claim` → `POST /hitl/reply` → `POST /hitl/resolve` |
| Approve SIM/eSIM/porting | `GET /approvals` → check the customer's step-up and request → `POST /approvals/{id}/approve` or `/reject` |
| Review or adjust policy | `GET /admin/policy`; tightening via `PATCH /admin/policy/actions/{action}`; every change is audited |
| Announce restoration | `POST /admin/outage/restore {alarm_id}` after the network team confirms |
| Check a customer complaint of "wrong answer" | find the trace id, pull audit records, compare tool results with the reply |

## 5. Incident playbooks

**Payment shows pending or unknown for long.** Look up `payment_id` in audit; call reconcile (the assistant does so on the customer's next payment-status question). Do not ask the customer to pay again until the provider's authoritative status is FAILED. If the provider says SUCCESS but the top-up is missing, apply it through OCS manually and note the case.

**Enterprise system down.** Circuit breaker opens; replies say the system is unavailable and offer handover. Verify with the system owner, then wait for the breaker to close (it half-opens automatically), or restart the process to reset it. Customers are never told an action succeeded while the system is down.

**Suspected abuse or injection.** Query `/admin/audit?event_type=UNSAFE_ACTION_BLOCKED`; check `unsafe_action_blocks` trend; block the subscriber at the gateway/WAF if needed. Confirm the audit chain is valid.

**OTP lockouts rising.** Check for SMS delivery problems (notification adapter or provider) before suspecting attack.

**Audit chain invalid.** Stop approvals, preserve the process memory and logs, escalate to security. In this reference build the chain is in memory, so a restart destroys the evidence: capture `/admin/audit` first.

## 6. Backup, restart, recovery

State is in memory. A restart clears conversations, pending actions, consent records, audit and queues. For a real deployment: persist these stores (Redis for session/pending actions, Postgres for consent and cases, write-once storage for audit) behind the existing class interfaces, and add a recovery step that reconciles any `OUTCOME_UNKNOWN` payments after restart. Until then, restart outside low-traffic windows and expect customers to be asked to repeat their request.

## 7. Release and rollback

1. CI runs tests and the diagram parse check (`.github/workflows/ci.yml`).
2. Tag the release; build the image; deploy to staging; run `make test` plus the journeys in `tests/integration/test_journeys.py` against the staging adapters.
3. Roll out gradually; watch the KPIs in §3.
4. Roll back by redeploying the previous image tag. Policy changes made through the admin API are not versioned in a store here; keep `config/policies.json` in version control and treat API patches as temporary.
5. Never roll back past a change that altered a stored schema without a migration plan (no schema exists today).

## 8. Data handling

Conversations hold at most 1000 characters per turn and are subject to a TTL. Card-like numbers, PINs and OTPs are masked before logging or audit. The policy pack lists intended retention (conversation 30 days, audit 730, KPI events 365); the reference build does not enforce those periods beyond its in-memory TTL. Deletion and residency requirements need operator and legal decisions (see `docs/BRD.md` §8).
