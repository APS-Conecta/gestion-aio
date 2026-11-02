#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Nextcloud accepts more than 100 users: our thin image drops upstream's cap.
set -euo pipefail
"$MACHINE" sudo bash -s << 'USERS'
set -euo pipefail
docker exec --user www-data nextcloud-aio-nextcloud bash -c '
  OC_PASS="$(head -c 24 /dev/urandom | base64)"; export OC_PASS
  for i in $(seq 101); do php occ user:add --password-from-env "prueba$i" > /dev/null || exit 1; done'
USERS
"$MACHINE" "sudo docker exec --user www-data nextcloud-aio-nextcloud php occ user:list --output=json" | jq -e 'length > 101'
