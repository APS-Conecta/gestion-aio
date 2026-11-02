#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Starts the suite through the mastercontainer's own web API with the requests its interface sends from
# a same-origin browser: read the initial passphrase from /setup, log in, set a test domain (validation
# skipped, the API's own switch), switch the optional containers off as upstream's initial-setup test
# does, and start.
# Usage: start-suite.sh <domain>
set -euo pipefail
domain=$1
base=https://localhost:8080
state="$(mktemp -d)"
trap 'rm -rf "$state"' EXIT
api() { curl -ksfS -b "$state/jar" -c "$state/jar" -H 'Sec-Fetch-Site: same-origin' "$@"; }

for _ in $(seq 60); do api -o /dev/null "$base/login" && break; sleep 5; done
api "$base/setup" | sed -n 's/.*id="initial-password"[^>]*>\([^<]*\)<.*/\1/p' | tr -d '\n' > "$state/passphrase"
[ -s "$state/passphrase" ] || { echo "no initial passphrase on /setup" >&2; exit 1; }
api "$base/login" > "$state/login.html"
csrf_name="$(sed -n 's/.*name="csrf_name" value="\([^"]*\)".*/\1/p' "$state/login.html" | head -n 1)"
csrf_value="$(sed -n 's/.*name="csrf_value" value="\([^"]*\)".*/\1/p' "$state/login.html" | head -n 1)"
post() { api -o /dev/null -d "csrf_name=$csrf_name" --data-urlencode "csrf_value=$csrf_value" "$@"; }

post --data-urlencode "password@$state/passphrase" "$base/api/auth/login"
api -o /dev/null "$base/containers" # the page after login; it joins the mastercontainer to the suite network
post -d skip_domain_validation=1 --data-urlencode "domain=$domain" "$base/api/configuration"
post -d options-form=1 -d office_suite_choice= "$base/api/configuration"
post --max-time 1800 "$base/api/docker/start"
