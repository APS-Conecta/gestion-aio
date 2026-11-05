#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Claims or releases the name of a DEIS code in a name-service deployment and prints the answer. A claim's
# answer holds the credential. CLOUDFLARE_API_TOKEN, when the environment has it, goes with any request as APS's
# token: a release needs it, and so does a claim of a test establishment's name.
# Usage: names.sh <production|ci> <claim|release> <DEIS code>
set -euo pipefail
usage() { echo "usage: $0 <production|ci> <claim|release> <DEIS code>" >&2; exit 2; }
[ "$#" -eq 3 ] || usage
case "$1" in
  production) url=https://gestion-nombres.apsconecta.workers.dev ;;
  ci) url=https://gestion-nombres-ci.apsconecta.workers.dev ;;
  *) usage ;;
esac
[[ "$3" =~ ^[0-9]+$ ]] || { echo "::error::'$3' is not a DEIS code" >&2; exit 2; }
request=(curl -sS --fail-with-body --retry 3 -X POST "$url/$2" -H 'Content-Type: application/json'
         --data "{\"code\":\"$3\"}")
case "$2" in
  claim) ;;
  release)
    [ -n "${CLOUDFLARE_API_TOKEN:-}" ] \
      || { echo "::error::The repository secret CLOUDFLARE_API_TOKEN is missing, so no name was released."; exit 1; } ;;
  *) usage ;;
esac
if [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
  printf 'Authorization: Bearer %s\n' "$CLOUDFLARE_API_TOKEN" | "${request[@]}" -H @-
else
  "${request[@]}"
fi
echo
