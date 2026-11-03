#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The name's address record holds the server's inside address and follows a change: the host's timer runs the
# updater every 10 minutes (run once here, as the timer does), it publishes the new address through the name
# service, and apsconecta.cl's own nameserver answers it. From inside the suite's containers, HTTPS to the name
# reaches the suite even while the host drops what its containers send it.
set -euo pipefail
name="$("$MACHINE" 'sudo docker exec nextcloud-aio-mastercontainer cat /mnt/docker-aio-config/data/configuration.json' | jq -r '.domain // empty')"
[ -n "$name" ] || { echo "the suite has no name: «Dirección» claimed none" >&2; exit 1; }
moved=192.168.77.11
ns="$(dig +short NS apsconecta.cl | head -n 1)"
record() { # <address>: the zone's nameserver answers it for the name within two minutes
  local answer
  for _ in $(seq 24); do
    answer="$(dig +short A "$name" "@$ns")"
    [ "$answer" = "$1" ] && { echo "$name holds $1"; return 0; }
    sleep 5
  done
  echo "$name holds ${answer:-nothing}, not $1" >&2
  return 1
}
update() { "$MACHINE" 'sudo systemctl start aps-address.service'; }

timer="$("$MACHINE" 'systemctl is-active aps-address.timer; systemctl show aps-address.timer --property=TimersMonotonic')"
echo "$timer"
if ! grep -qx active <<< "$timer" || ! grep -q 'OnUnitActiveUSec=10min' <<< "$timer"; then
  echo "the inside-address timer does not run every 10 minutes" >&2
  exit 1
fi
record "$ADDRESS"
# shellcheck disable=SC2016 # expands on the machine
"$MACHINE" sudo bash -s "$ADDRESS" "$moved" << 'MOVE'
set -euo pipefail
route="$(ip -4 route show default | head -n 1)"
echo "$route" > /run/aps-default-route
dev="$(awk '{for (i = 1; i < NF; i++) if ($i == "dev") print $(i + 1)}' <<< "$route")"
ip address add "$2/24" dev "$dev"
# shellcheck disable=SC2086 # the route's words
ip route replace ${route/ src $1/} src "$2"
MOVE
update
record "$moved"
# shellcheck disable=SC2016 # expands on the machine
"$MACHINE" sudo bash -s "$moved" << 'BACK'
set -euo pipefail
route="$(cat /run/aps-default-route)"
dev="$(awk '{for (i = 1; i < NF; i++) if ($i == "dev") print $(i + 1)}' <<< "$route")"
# shellcheck disable=SC2086 # the route's words
ip route replace $route
ip address del "$1/24" dev "$dev"
BACK
update
record "$ADDRESS"

"$MACHINE" "sudo docker exec -i nextcloud-aio-nextcloud sh -c 'cat > /tmp/letsencrypt-staging-roots.pem'" \
  < aps/acceptance/letsencrypt-staging-roots.pem
# shellcheck disable=SC2016 # expands on the machine
"$MACHINE" sudo bash -s "$ADDRESS" "$name" << 'DROP'
set -euo pipefail
rule=(PREROUTING -i br-+ -m addrtype --dst-type LOCAL -j DROP)
iptables -t raw -I "${rule[@]}"
trap 'iptables -t raw -D "${rule[@]}"' EXIT
if docker exec nextcloud-aio-nextcloud curl --silent --insecure --max-time 10 --output /dev/null "https://$1/"; then
  echo "the host still answers its containers at $1" >&2
  exit 1
fi
echo "the host drops what its containers send it; from inside Nextcloud, https://$2/status.php answers:"
docker exec nextcloud-aio-nextcloud curl --fail --silent --show-error --max-time 20 \
  --cacert /tmp/letsencrypt-staging-roots.pem "https://$2/status.php"
DROP
