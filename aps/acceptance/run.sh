#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The clean-machine acceptance run (Seam 1), for a disposable GitHub runner with KVM only: boot a fresh
# Debian 12 VM on a network it shares with the runner, as a laptop shares the clinic's; run the one
# command in it as an operator would; then run every check in aps/acceptance/checks/ in name order.
#
# A check is an executable file aps/acceptance/checks/<NN>-<name>.<sh|py>; NN orders it. It runs on the
# runner, from the repo root, with:
#   TAG       the pre-release under test
#   COMMAND   the one command, as the manual prints it
#   CONSOLE   a file holding what the first run of COMMAND printed, one-time link included; it stays out of
#             the evidence, and one-command.sh runs COMMAND again with the link's secret masked
#   MACHINE   runs one shell command line on the VM as the operator, whose sudo needs no password; like
#             ssh, it passes stdin through: "$MACHINE" "sudo docker ps"
#   ADDRESS   the VM's address on the shared network, where the runner's browser reaches it
#   EVIDENCE  an empty directory for the check's own files, such as screenshots
# python3 has Playwright with Chromium (aps-acceptance.yml installs it); browser.py is the one way to open it.
# Exit 0 passes. A failing check is named in the log, its EVIDENCE gets every container's log (the name's
# credential and AIO's passphrase blanked out) and the disk usage, and the workflow attaches it to the run. The
# check that has the wizard claim a CI name writes its DEIS code as code= to $GITHUB_OUTPUT first, and the workflow
# releases it.
# Usage: run.sh <tag>
set -euo pipefail
[[ "$#" -eq 1 && "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] || { echo "usage: $0 <tag>" >&2; exit 2; }
[[ "${GITHUB_ACTIONS:-}" = true && -c /dev/kvm ]] || { echo "$0 runs on a disposable GitHub runner with KVM only" >&2; exit 2; }
cd "$(git rev-parse --show-toplevel)"
work="$RUNNER_TEMP/acceptance"
mkdir -p "$work/evidence"
export TAG="$1" ADDRESS=192.168.77.10 MACHINE="$work/machine" CONSOLE="$work/console.txt"
export COMMAND="curl -fsSL https://github.com/$GITHUB_REPOSITORY/releases/download/$TAG/install.sh | sudo bash"

password="" passphrase=""
secret() { # <variable> <command>: the password in the JSON the command prints on the machine, masked in the log once found
  [ -z "${!1}" ] || return 0
  printf -v "$1" '%s' "$("$MACHINE" "$2" 2> /dev/null | jq -r '.password // empty' 2> /dev/null || true)"
  [ -z "${!1}" ] || printf '\n::add-mask::%s\n' "${!1}"
}
mask_secrets() { # once «Dirección» claimed the name: its credential and AIO's passphrase, each on a line of its own
  secret password 'sudo cat /var/lib/aps-conecta/name-service.json'
  secret passphrase 'sudo docker exec nextcloud-aio-mastercontainer cat /mnt/docker-aio-config/data/configuration.json'
}
collect() { # <dir>: every container's log, without the name's credential and AIO's passphrase, and the disk usage
  local name log
  mkdir -p "$1"
  df -h > "$1/runner-disk.txt"
  "$MACHINE" 'df -h; sudo docker system df; sudo docker ps --all' > "$1/machine.txt" 2>&1 || true
  mask_secrets
  for name in $("$MACHINE" "sudo docker ps --all --format '{{.Names}}'" 2> /dev/null || true); do
    log="$("$MACHINE" "sudo docker logs --timestamps $name" 2>&1 || true)"
    [ -z "$password" ] || log="${log//"$password"/[credential]}"
    [ -z "$passphrase" ] || log="${log//"$passphrase"/[passphrase]}"
    printf '%s\n' "$log" > "$1/$name.log"
  done
}
on_exit() { # a failure outside the checks names its stage and collects the same evidence
  local rc=$?
  [ "$rc" -eq 0 ] || [ -z "$stage" ] || { echo "::error title=Acceptance run failed::$stage"; collect "$work/evidence/run"; }
}
stage="boot a fresh Debian 12 VM"
trap on_exit EXIT

image=debian-12-genericcloud-amd64-20261026-2643.qcow2
curl -fsSL --retry 5 -o "$work/debian.qcow2" "https://cloud.debian.org/images/cloud/bookworm/20261026-2643/$image"
echo "0464b5d4ae3aa4f658468d88da83af7857b71548b30d46dcb6282770955a747fb95a983476db90d25afd0653af607224c7ebd05122ab71faa106fd712aaad652  $work/debian.qcow2" \
  | sha512sum --check --quiet
qemu-img create -q -f qcow2 -F qcow2 -b "$work/debian.qcow2" "$work/disk.qcow2" 60G

# The shared network: a tap device, DHCP and DNS from dnsmasq, NAT to the internet. Its DNS has no answer for
# Let's Encrypt's production service: the suite gets its certificate from the staging one (checks/25-certificate.py).
sudo ip tuntap add dev aps0 mode tap
sudo ip address add 192.168.77.1/24 dev aps0
sudo ip link set aps0 up
sudo sysctl -qw net.ipv4.ip_forward=1
sudo iptables -t nat -A POSTROUTING -s 192.168.77.0/24 ! -o aps0 -j MASQUERADE
sudo iptables -I FORWARD -i aps0 -j ACCEPT
sudo iptables -I FORWARD -o aps0 -j ACCEPT
sudo dnsmasq --interface=aps0 --except-interface=lo --bind-interfaces --dhcp-range="$ADDRESS,$ADDRESS,12h" \
  --address=/acme-v02.api.letsencrypt.org/

ssh-keygen -q -t ed25519 -N '' -C acceptance -f "$work/key"
printf '#cloud-config\nssh_authorized_keys:\n  - %s\n' "$(cat "$work/key.pub")" > "$work/user-data"
printf 'instance-id: gestion-acceptance\nlocal-hostname: gestion\n' > "$work/meta-data"
cloud-localds "$work/seed.img" "$work/user-data" "$work/meta-data"
: > "$work/evidence/serial.log" # the runner's own file, so the evidence upload can read it
sudo qemu-system-x86_64 -accel kvm -cpu host -smp 4 -m 12G \
  -drive "file=$work/disk.qcow2,if=virtio,format=qcow2" -drive "file=$work/seed.img,if=virtio,format=raw" \
  -netdev tap,id=lan,ifname=aps0,script=no,downscript=no -device virtio-net-pci,netdev=lan \
  -display none -serial "file:$work/evidence/serial.log" -daemonize

cat > "$work/ssh_config" << SSH
Host machine
  HostName $ADDRESS
  User debian
  IdentityFile $work/key
  UserKnownHostsFile $work/known_hosts
  StrictHostKeyChecking accept-new
  BatchMode yes
  ConnectTimeout 10
  ServerAliveInterval 30
  LogLevel ERROR
SSH
printf '#!/bin/sh\nexec ssh -F "%s" machine "$@"\n' "$work/ssh_config" > "$MACHINE"
chmod +x "$MACHINE"
for _ in $(seq 60); do "$MACHINE" true 2> /dev/null && break; sleep 5; done
"$MACHINE" 'sudo cloud-init status --wait' > /dev/null
# shellcheck disable=SC2016 # expands on the VM
"$MACHINE" '. /etc/os-release; echo "$PRETTY_NAME, $(uname -m)"; [ "$ID $VERSION_ID" = "debian 12" ]'

stage="the one command"
echo "$COMMAND"
aps/acceptance/one-command.sh "$CONSOLE"

stage=""
failed=()
for check in aps/acceptance/checks/*; do
  name="$(basename "${check%.*}")"
  export EVIDENCE="$work/evidence/$name"
  mkdir -p "$EVIDENCE"
  echo "::group::$name"
  if timeout 15m "$check" < /dev/null; then
    mask_secrets
    echo "::endgroup::"
    echo "passed: $name"
  else
    echo "::endgroup::"
    echo "::error title=Acceptance check failed::$name"
    collect "$EVIDENCE"
    failed+=("$name")
  fi
done
[ "${#failed[@]}" -eq 0 ] || { echo "failed: ${failed[*]}"; exit 1; }
echo "acceptance: every check passed"
