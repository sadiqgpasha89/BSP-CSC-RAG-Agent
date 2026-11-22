#!/usr/bin/env bash
# Deploy the sandbox build to Vercel (Hobby plan is free; no billing details needed).
#
#   bash deploy/vercel/deploy.sh
#
# Needs Node.js (for npx) and a Vercel account: the script runs `vercel login` if you are not signed in.
# It sets ENVIRONMENT=staging plus generated SECRET_KEY / ADMIN_API_KEY as production environment variables,
# so the operator endpoints are NOT open on a public URL. Re-running updates the deployment; existing
# environment variables are left as they are.
set -euo pipefail
cd "$(dirname "$0")/../.."
command -v npx >/dev/null || { echo "Node.js (npx) is required: https://nodejs.org"; exit 1; }
V="npx --yes vercel@latest"

$V whoami >/dev/null 2>&1 || $V login
$V link --yes >/dev/null   # creates/links the project from this folder name

has_env() { $V env ls production 2>/dev/null | grep -qE "^\s*$1\b"; }
set_env() {  # name value
  has_env "$1" || { printf '%s' "$2" | $V env add "$1" production >/dev/null; echo "set $1"; }
}
NEW_KEY=""
if ! has_env ADMIN_API_KEY; then NEW_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"; fi
set_env ENVIRONMENT staging
set_env LOG_LEVEL INFO
has_env SECRET_KEY || set_env SECRET_KEY "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
[ -n "$NEW_KEY" ] && set_env ADMIN_API_KEY "$NEW_KEY"

URL="$($V deploy --prod --yes | tail -n1)"
echo
echo "Deployed: $URL"
echo "  Customer chat : $URL/customer"
echo "  Operator UI   : $URL/   (asks for the operator key)"
echo "  Health        : $URL/api/v1/health"
[ -n "$NEW_KEY" ] && echo "Operator key (X-API-Key), shown once: $NEW_KEY"
echo "Sandbox data only. State is per serverless instance; see docs/DEPLOYMENT.md before relying on multi-step flows."
