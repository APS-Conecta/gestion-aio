#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# A second run of the one command, after «Listo», changes nothing: containers, volumes, networks, images, the bundle,
# apt's configuration and the installed packages stay as they were. It prints the suite's address and no link.
set -euo pipefail
snapshot() {
  "$MACHINE" sudo bash -s << 'STATE'
set -euo pipefail
docker ps --all --quiet | xargs --no-run-if-empty docker inspect --format '{{.Name}} {{.Id}} {{.Image}} {{.State.StartedAt}}' | sort
docker volume ls --quiet | sort
docker network ls --quiet --no-trunc | sort
docker image ls --all --quiet --no-trunc | sort -u
find /opt/aps-conecta /etc/apt -type f -exec sha256sum {} + | sort -k 2
dpkg-query --show
STATE
}
before="$(snapshot)"
console="$(mktemp)" # a one-time link, were one printed, stays out of the log and the evidence
aps/acceptance/one-command.sh "$console"
printed="$(cat "$console")"
rm -f "$console"
after="$(snapshot)"
diff <(printf '%s\n' "$before") <(printf '%s\n' "$after") || { echo "the second run changed the machine (above)" >&2; exit 1; }
name="$("$MACHINE" 'sudo docker exec nextcloud-aio-mastercontainer cat /mnt/docker-aio-config/data/configuration.json' | jq -r '.domain // empty')"
grep -qF "https://$name" <<< "$printed" || { echo "the second run did not print the suite's address" >&2; exit 1; }
if grep -q 'https://[^[:space:]]*#' <<< "$printed"; then
  echo "the second run printed a one-time link after «Listo»" >&2
  exit 1
fi
echo "the second run changed nothing and printed the suite's address, https://$name, and no link"
