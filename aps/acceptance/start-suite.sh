#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Starts the suite through the mastercontainer's own web API with the requests its interface sends from
# a same-origin browser: log in with the passphrase AIO's first-run setup stored (the wizard's suite port ran
# that setup on «Dirección», which also set the free domain), switch the optional containers off as upstream's
# initial-setup test does, and start.
# Usage: start-suite.sh
set -euo pipefail
base=https://localhost:8080
state="$(mktemp -d)"
trap 'rm -rf "$state"' EXIT
api() { curl -ksfS -b "$state/jar" -c "$state/jar" -H 'Sec-Fetch-Site: same-origin' "$@"; }

for _ in $(seq 60); do api -o /dev/null "$base/login" && break; sleep 5; done
sudo docker exec nextcloud-aio-mastercontainer cat /mnt/docker-aio-config/data/configuration.json \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["password"], end="")' > "$state/passphrase"
[ -s "$state/passphrase" ] || { echo "AIO stored no passphrase" >&2; exit 1; }
api "$base/login" > "$state/login.html"
csrf_name="$(sed -n 's/.*name="csrf_name" value="\([^"]*\)".*/\1/p' "$state/login.html" | head -n 1)"
csrf_value="$(sed -n 's/.*name="csrf_value" value="\([^"]*\)".*/\1/p' "$state/login.html" | head -n 1)"
post() { api -o /dev/null -d "csrf_name=$csrf_name" --data-urlencode "csrf_value=$csrf_value" "$@"; }

post --data-urlencode "password@$state/passphrase" "$base/api/auth/login"
api -o /dev/null "$base/containers" # the page after login; it joins the mastercontainer to the suite network
post -d options-form=1 -d office_suite_choice= "$base/api/configuration"
post --max-time 1800 "$base/api/docker/start"
