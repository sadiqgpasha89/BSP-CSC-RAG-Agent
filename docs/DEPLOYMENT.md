# Deployment: Google Cloud Run (recommended) vs Vercel

## Decision

**Use Google Cloud Run.** Vercel is not a good fit for this build.

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

The Dockerfile, `deploy.sh` and workflow were written without access to a Google Cloud project or network in the authoring environment: the shell script passes a syntax check and the workflow parses as YAML, but **no image build or deployment was run**. Expect to fix small issues on the first deploy (for example an IAM permission or a missing dependency in the image) and run the smoke test above.
