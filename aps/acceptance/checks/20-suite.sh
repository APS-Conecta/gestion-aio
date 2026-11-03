#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The pinned suite comes up: the mastercontainer starts it (through start-suite.sh until the Provisionador
# does), every container it started runs an image the release pins by digest, and Nextcloud turns healthy.
set -euo pipefail
"$MACHINE" bash -s example.com < aps/acceptance/start-suite.sh
lock="$("$MACHINE" cat /opt/aps-conecta/gestion/images.lock)"
started="$("$MACHINE" "sudo docker ps --all --format '{{.Names}}'" | { grep -vx nextcloud-aio-mastercontainer || true; })"
for name in $started; do
  image="$("$MACHINE" "sudo docker inspect -f '{{.Config.Image}}' $name")"
  echo "$name $image"
  grep -qxF "$image" <<< "$lock" || { echo "$name runs $image, which $TAG does not pin" >&2; exit 1; }
done
grep -qx nextcloud-aio-nextcloud <<< "$started" || { echo "the mastercontainer did not start Nextcloud" >&2; exit 1; }
for _ in $(seq 90); do
  [ "$("$MACHINE" "sudo docker inspect -f '{{.State.Health.Status}}' nextcloud-aio-nextcloud")" = healthy ] && exit 0
  sleep 10
done
echo "Nextcloud did not turn healthy within 15 minutes" >&2
exit 1
