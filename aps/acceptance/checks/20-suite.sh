#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The suite «Instalar» started runs at the name «Dirección» claimed: AIO started exactly the core, our Caddy image as
# the reverse proxy and the chosen components (Talk's recording server among them when AIO's switch for it is on;
# 70-talk checks the switch follows the hardware), each from an image the release pins by digest, and every one runs,
# healthy where it has a health check. The machine's memory use is printed then.
set -euo pipefail
chosen=(nextcloud-aio-apache nextcloud-aio-caddy nextcloud-aio-clamav nextcloud-aio-database nextcloud-aio-eurooffice
  nextcloud-aio-fulltextsearch nextcloud-aio-harp nextcloud-aio-imaginary nextcloud-aio-nextcloud
  nextcloud-aio-notify-push nextcloud-aio-redis nextcloud-aio-talk)
aio='sudo docker exec nextcloud-aio-mastercontainer cat /mnt/docker-aio-config/data/configuration.json'
[ "$("$MACHINE" "$aio" | jq -r '.isTalkRecordingEnabled // false')" != true ] || chosen+=(nextcloud-aio-talk-recording)
lock="$("$MACHINE" cat /opt/aps-conecta/gestion/images.lock)"
started="$("$MACHINE" "sudo docker ps --all --format '{{.Names}}'" | { grep -vx nextcloud-aio-mastercontainer || true; } | sort)"
if [ "$started" != "$(printf '%s\n' "${chosen[@]}" | sort)" ]; then
  printf 'AIO started these containers, not exactly the core and the chosen components:\n%s\n' "$started" >&2
  exit 1
fi
for name in $started; do
  image="$("$MACHINE" "sudo docker inspect -f '{{.Config.Image}}' $name")"
  echo "$name $image"
  grep -qxF "$image" <<< "$lock" || { echo "$name runs $image, which $TAG does not pin" >&2; exit 1; }
done
format='{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}'
for _ in $(seq 90); do
  states="$("$MACHINE" "sudo docker inspect -f '$format' ${chosen[*]}")"
  if ! grep -qvE ' running (healthy|-)$' <<< "$states"; then
    echo "$states"
    "$MACHINE" free --mebi
    exit 0
  fi
  sleep 10
done
echo "$states"
echo "not every container ran healthy within 15 minutes" >&2
exit 1
