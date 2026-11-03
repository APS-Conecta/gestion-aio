#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# A second run of the one command changes nothing: containers, volumes, networks, images, the bundle,
# apt's configuration and the installed packages stay as they were.
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
"$MACHINE" "$COMMAND"
after="$(snapshot)"
diff <(printf '%s\n' "$before") <(printf '%s\n' "$after") || { echo "the second run changed the machine (above)" >&2; exit 1; }
echo "the second run changed nothing"
