#!/usr/bin/env bash
# Remove the Cloud Run service and its secrets. Usage: PROJECT_ID=... [REGION=...] [SERVICE=...] bash deploy/cloudrun/teardown.sh
set -euo pipefail
: "${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-asia-south1}"; SERVICE="${SERVICE:-bsp-csc-rag-agent}"
gcloud run services delete "$SERVICE" --region "$REGION" --project "$PROJECT_ID" --quiet
for s in bsp-secret-key bsp-admin-api-key; do gcloud secrets delete "$s" --project "$PROJECT_ID" --quiet || true; done
echo "Deleted. Container images in Artifact Registry (cloud-run-source-deploy) are kept; delete them in the console if you want."
