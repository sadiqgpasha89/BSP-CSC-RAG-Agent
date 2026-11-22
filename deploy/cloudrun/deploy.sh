#!/usr/bin/env bash
# Deploy the sandbox build of BSP-CSC-RAG-Agent to Google Cloud Run.
#
#   export PROJECT_ID=my-gcp-project            # required
#   export REGION=asia-south1                   # optional (default asia-south1)
#   export SERVICE=bsp-csc-rag-agent            # optional
#   export PUBLIC=true                          # optional: allow unauthenticated access (default true)
#   bash deploy/cloudrun/deploy.sh
#
# Needs: gcloud CLI, logged in (`gcloud auth login`), billing enabled on the project.
# Re-running is safe: secrets are only created once, the service is updated in place.
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-bsp-csc-rag-agent}"
PUBLIC="${PUBLIC:-true}"
cd "$(dirname "$0")/../.."

command -v gcloud >/dev/null || { echo "gcloud CLI not found: https://cloud.google.com/sdk/docs/install"; exit 1; }
gcloud config set project "$PROJECT_ID" >/dev/null

echo "==> Enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="${RUNTIME_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

mk_secret() {  # name, generator
  if ! gcloud secrets describe "$1" >/dev/null 2>&1; then
    echo "==> Creating secret $1"
    python3 -c "import secrets; print(secrets.token_urlsafe(48))" | gcloud secrets create "$1" --data-file=- --replication-policy=automatic >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "$1" --member="serviceAccount:${RUNTIME_SA}" --role="roles/secretmanager.secretAccessor" >/dev/null
}
mk_secret bsp-secret-key
mk_secret bsp-admin-api-key

AUTH_FLAG="--no-allow-unauthenticated"
[ "$PUBLIC" = "true" ] && AUTH_FLAG="--allow-unauthenticated"

echo "==> Building and deploying (Cloud Build uses ./Dockerfile)"
# min=max=1 instance: conversations, pending confirmations, consent and audit live in process memory,
# so more than one instance would split them (a customer's YES could land on an instance that never saw the request).
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --platform managed \
  --min-instances 1 --max-instances 1 \
  --cpu 1 --memory 1Gi --concurrency 40 --timeout 60 \
  --cpu-boost \
  "$AUTH_FLAG" \
  --set-env-vars "ENVIRONMENT=staging,LOG_LEVEL=INFO" \
  --set-secrets "SECRET_KEY=bsp-secret-key:latest,ADMIN_API_KEY=bsp-admin-api-key:latest"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo
echo "Deployed: $URL"
echo "  Customer chat : $URL/customer"
echo "  Operator UI   : $URL/        (asks for the operator key)"
echo "  Health        : $URL/api/v1/health"
echo "Operator key (X-API-Key):  gcloud secrets versions access latest --secret=bsp-admin-api-key"
echo "Remember: sandbox data and PINs only. Do not point real customers at this build."
