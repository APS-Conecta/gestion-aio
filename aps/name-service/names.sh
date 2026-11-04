#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Claims or releases the name of a DEIS code in a name-service deployment and prints the answer. A claim's
# answer holds the credential. A release needs CLOUDFLARE_API_TOKEN in the environment, or the name's own credential:
# the file holding the claim's answer.
# Usage: names.sh <production|ci> <claim|release> <DEIS code> [<claim's answer file, for a release>]
set -euo pipefail
usage() { echo "usage: $0 <production|ci> <claim|release> <DEIS code> [<claim's answer file>]" >&2; exit 2; }
[ "$#" -eq 3 ] || { [ "$#" -eq 4 ] && [ "$2" = release ]; } || usage
case "$1" in
  production) url=https://gestion-nombres.apsconecta.workers.dev ;;
  ci) url=https://gestion-nombres-ci.apsconecta.workers.dev ;;
  *) usage ;;
esac
[[ "$3" =~ ^[0-9]+$ ]] || { echo "::error::'$3' is not a DEIS code" >&2; exit 2; }
request=(curl -sS --fail-with-body -X POST "$url/$2" -H 'Content-Type: application/json' --data "{\"code\":\"$3\"}")
case "$2" in
  claim) "${request[@]}" ;;
  release)
    if [ "$#" -eq 4 ]; then
      [ -s "$4" ] || { echo "::error::No credential of $3's name was kept, so no name was released; if $3 holds one," \
        "the APS name release workflow releases it."; exit 1; }
      jq -r '"X-Api-User: \(.username)\nX-Api-Key: \(.password)"' "$4" | "${request[@]}" -H @-
    else
      [ -n "${CLOUDFLARE_API_TOKEN:-}" ] \
        || { echo "::error::The repository secret CLOUDFLARE_API_TOKEN is missing, so no name was released."; exit 1; }
      printf 'Authorization: Bearer %s\n' "$CLOUDFLARE_API_TOKEN" | "${request[@]}" -H @-
    fi ;;
  *) usage ;;
esac
echo
