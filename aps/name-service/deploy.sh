#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Deploys the name service from the checked-out tree: the slug table generated from the DEIS register is
# bundled, and CLOUDFLARE_API_TOKEN (from the environment) deploys the Worker and becomes its secret.
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
accounts="$(printf 'Authorization: Bearer %s\n' "$CLOUDFLARE_API_TOKEN" \
  | curl -fsS --retry 3 -H @- https://api.cloudflare.com/client/v4/accounts)"
[ "$(jq '.result | length' <<< "$accounts")" -eq 1 ] \
  || { echo "::error::CLOUDFLARE_API_TOKEN must reach exactly one Cloudflare account."; exit 1; }
CLOUDFLARE_ACCOUNT_ID="$(jq -r '.result[0].id' <<< "$accounts")"
export CLOUDFLARE_ACCOUNT_ID WRANGLER_SEND_METRICS=false
wrangler=(npx --yes wrangler@4.157.0 --config aps/name-service/wrangler.jsonc --env "$env")
"${wrangler[@]}" deploy
printf '%s' "$CLOUDFLARE_API_TOKEN" | "${wrangler[@]}" secret put CLOUDFLARE_API_TOKEN
