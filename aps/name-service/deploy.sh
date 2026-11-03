#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Deploys the name service from the checked-out tree: the slug table generated from the DEIS register is
# bundled, the locked wrangler deploys it with CLOUDFLARE_API_TOKEN (from the environment), which also becomes
# the Worker's secret.
# Usage: deploy.sh <production|ci>
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
case "${1:-}" in
  production) env= ;;
  ci) env=ci ;;
  *) echo "usage: $0 <production|ci>" >&2; exit 2 ;;
esac
[ -n "${CLOUDFLARE_API_TOKEN:-}" ] \
  || { echo "::error::The repository secret CLOUDFLARE_API_TOKEN is missing, so the name service was not deployed."; exit 1; }
python3 -I aps/provisionador/deis.py slugs > aps/name-service/slugs.json
cloudflare() {
  printf 'Authorization: Bearer %s\n' "$CLOUDFLARE_API_TOKEN" \
    | curl -sS --fail-with-body --retry 3 -H @- -H 'Content-Type: application/json' "${@:2}" "https://api.cloudflare.com/client/v4$1"
}
accounts="$(cloudflare /accounts)"
[ "$(jq '.result | length' <<< "$accounts")" -eq 1 ] \
  || { echo "::error::CLOUDFLARE_API_TOKEN must reach exactly one Cloudflare account."; exit 1; }
CLOUDFLARE_ACCOUNT_ID="$(jq -r '.result[0].id' <<< "$accounts")"
export CLOUDFLARE_ACCOUNT_ID WRANGLER_SEND_METRICS=false
# The name service is served on the account's workers.dev subdomain, apsconecta.workers.dev.
if ! cloudflare "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain" > /dev/null 2>&1; then
  cloudflare "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain" -X PUT --data '{"subdomain":"apsconecta"}'
fi
npm ci --prefix aps/name-service --no-audit --no-fund --silent
wrangler=(aps/name-service/node_modules/.bin/wrangler --config aps/name-service/wrangler.jsonc --env "$env")
"${wrangler[@]}" deploy
printf '%s' "$CLOUDFLARE_API_TOKEN" | "${wrangler[@]}" secret put CLOUDFLARE_API_TOKEN
