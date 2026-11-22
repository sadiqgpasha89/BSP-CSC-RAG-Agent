# Deployment: Vercel (free) and Google Cloud Run

Two targets are supported. **Vercel** needs no billing details but cannot keep this app's in-memory state reliably (see below). **Cloud Run** can, with a single instance, but needs a billing account. Pick Vercel for a free look-around demo, Cloud Run if multi-step flows must work every time.

## Vercel (Hobby plan, no billing)

### Deploy

Option A, script (needs Node.js; opens a browser for `vercel login`):
```bash
bash deploy/vercel/deploy.sh
```
It links the project, sets `ENVIRONMENT=staging`, a generated `SECRET_KEY` and `ADMIN_API_KEY` as production environment variables, runs `vercel deploy --prod`, and prints the URLs and the operator key once.

Option B, dashboard: add a new project from the GitHub repository (Vercel detects FastAPI), add the environment variables `ENVIRONMENT=staging`, `SECRET_KEY` (>= 32 random characters) and `ADMIN_API_KEY`, then deploy. Variables must be enabled for **Preview** as well if you deploy a branch before merging to `main`; the production deployment only follows `main`.

Configuration in the repo: `pyproject.toml` (`[tool.vercel] entrypoint = "src.api.app:app"`), `vercel.json` (60 s max duration, excludes tests/docs/diagrams from the bundle), `.vercelignore`. Vercel uses Python 3.12 by default. Dependencies come from `pyproject.toml`/`uv.lock` (kept in sync with `uv lock --check`).

### What to expect

Each Vercel function instance has its own memory, and instances start, stop and multiply on demand (Vercel's Fluid compute reuses a warm instance for later requests, but does not guarantee it). This app keeps conversations, pending confirmations, OTP challenges, consent, audit, the handover queue, the approvals queue and the KPI buffer in process memory. So:

| Works on any instance | May fail or look reset when the next request lands on another instance |
|---|---|
| `/customer` page, sign-in (signed token), balance and usage, bill explanation, plan lists, order and outage lookups, knowledge answers, health check | Confirm/cancel after "Recharge", "Pay", plan change, complaint; SIM/eSIM step-up code; consent-then-resume; handover claim/reply; operator approvals; KPI and audit views (they show only the instance you hit) |

For a single demo user clicking through slowly, the same instance often serves consecutive requests, so many flows will work, but a cold start or a second visitor can break them mid-flow. Symptoms are replies like "nothing waiting for your confirmation" or an empty approvals list. That is the platform behaving safely (it never invents state), not data loss.

Other limits: Hobby is for personal, non-commercial use under Vercel's terms; bundle limit 500 MB uncompressed for Python (numpy, pandas, scipy and scikit-learn should fit, but this was not measured here; set `VERCEL_ANALYZE_BUILD_OUTPUT=1` if the build reports the bundle is too large); the first request after idle includes cold-start work (knowledge base loading and index build).

### Making Vercel reliable

Move conversations, pending actions, OTP challenges, consent, queues and audit to an external store (for example Redis from the Vercel Marketplace) behind the existing store classes (`docs/SADR.md` ADR-009). Then multi-step flows work across instances. That change is not implemented.

---

## Google Cloud Run (recommended when state must hold)

| Factor | Cloud Run | Vercel |
|---|---|---|
| Process model | Long-lived container; one uvicorn process keeps memory between requests | Serverless functions; instances start, stop and multiply per request |
| In-memory state (conversations, **pending confirmations**, OTP challenges, consent, audit chain, handover queue) | Works with one instance (`min=max=1`) | Breaks: a customer's "YES" can land on a fresh instance that never saw the pending payment, so confirmations, step-up and handover fail or reset |
| Dependencies (numpy, pandas, scipy, scikit-learn, networkx) | Normal container image | Heavy for function bundle limits and cold starts |
| Startup work (loads the knowledge base and builds retrieval indexes) | Done once, instance kept warm | Repeated on cold starts |
| Operator console and customer page | Served by the same app | Would also work as static assets, but the API behind them is the problem |
| Fit with later work (Redis, Postgres, Secret Manager, IAM, LLM on Vertex) | Native | Possible but separate vendors |

Vercel could host a static front end later, with the API on Cloud Run, but that is not needed now.

**The one-instance limit is deliberate.** `max-instances=1` is required until conversations, pending actions, consent and audit move to Redis/Postgres (see `docs/SADR.md` ADR-009). With one instance, a revision rollout or restart still clears all state, and customers are asked to repeat their request.

## What gets deployed

The sandbox build: mock enterprise systems and demo subscribers with a sandbox PIN printed on the page. **It is not for real customers.** `deploy.sh` sets `ENVIRONMENT=staging`, which makes the app require `SECRET_KEY` and `ADMIN_API_KEY` and puts the operator endpoints (approvals, policy, audit, KPIs, handover queue) behind the `X-API-Key` header, so a public URL does not expose an open console. The customer page `/customer` works without the operator key.

Notes for `staging`:
- The operator console's built-in chat tab sends no session token, so outside development it runs at the lowest assurance level. Use `/customer` for end-to-end chat.
- The console asks for the operator key when it first calls a protected endpoint; the key is kept only for the browser session.

## One-time prerequisites

1. A Google Cloud project with billing enabled.
2. `gcloud` installed and logged in: `gcloud auth login`.
3. Python 3 on the machine running the script (used only to generate random secrets).

## Deploy

```bash
export PROJECT_ID=your-project-id
export REGION=asia-south1          # pick the region nearest your users
bash deploy/cloudrun/deploy.sh
```

The script enables the Cloud Run, Cloud Build, Artifact Registry and Secret Manager APIs; creates the secrets `bsp-secret-key` and `bsp-admin-api-key` (once); grants the runtime service account access to them; builds the image from `Dockerfile` with Cloud Build; deploys with `min=max=1` instance, 1 vCPU, 1 GiB, concurrency 40 and startup CPU boost; and prints the URLs. Set `PUBLIC=false` to require Google IAM authentication instead of allowing anonymous access.

Get the operator key: `gcloud secrets versions access latest --secret=bsp-admin-api-key`

Smoke test after deploy:
```bash
curl "$URL/api/v1/health"
# open $URL/customer, sign in with a demo subscriber, send "Explain my bill"
# open $URL/ and the Measured KPIs tab (enter the operator key when asked)
```

## Deploy from GitHub Actions (optional)

`.github/workflows/deploy-cloudrun.yml` is a manual (`workflow_dispatch`) workflow using Workload Identity Federation, so no service-account key is stored. Setup once:

1. Create a deployer service account with roles `run.admin`, `cloudbuild.builds.editor`, `artifactregistry.writer`, `storage.admin` (for the Cloud Build source bucket) and `iam.serviceAccountUser` on the runtime service account.
2. Create a Workload Identity Pool and GitHub OIDC provider restricted to this repository, and allow it to impersonate the deployer service account.
3. In the repository settings add variables `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`.
4. Run `deploy.sh` once locally (it creates the secrets), then trigger the workflow.

Follow Google's current guide for the exact commands; they change over time.

## Cost and operation

`min-instances=1` keeps one instance running so state survives between requests; expect a small continuous charge for it plus Cloud Build and Artifact Registry storage. Check current Cloud Run pricing for your region before leaving it on. Remove everything with `PROJECT_ID=... bash deploy/cloudrun/teardown.sh`.

Logs: Cloud Logging (structured JSON from structlog). Use the `trace_id` from a reply with `/api/v1/admin/audit?correlation_id=`.

## Before any real traffic

These are open items from `IMPLEMENTATION_STATUS.md`, not optional for production:

1. Externalise state (Redis for sessions/pending actions, Postgres for consent and cases, write-once storage for audit), then raise `max-instances`.
2. Replace the sandbox PIN login with the operator's identity provider.
3. Replace sandbox adapters with real integrations (`docs/TSD.md` §5).
4. Add rate limiting and a WAF (Cloud Armor) in front, plus alerting and OpenTelemetry export.
5. Confirm retention, residency and compliance items in `docs/BRD.md` §8.

## Not verified

**Vercel:** `vercel.json`, `.vercelignore`, `deploy/vercel/deploy.sh` and the `[tool.vercel]` entrypoint were written from Vercel's current FastAPI and Python documentation; `uv lock --check` passes and the script passes a shell syntax check, but no Vercel build or deployment was run, the bundle size was not measured, and FastAPI was not installed in the authoring environment to import the app.

**Cloud Run:** The Dockerfile, `deploy.sh` and workflow were written without access to a Google Cloud project or network in the authoring environment: the shell script passes a syntax check and the workflow parses as YAML, but **no image build or deployment was run**. Expect to fix small issues on the first deploy (for example an IAM permission or a missing dependency in the image) and run the smoke test above.
