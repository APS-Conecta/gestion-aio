#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The bootstrap. The operator runs it as the one command:
#   curl -fsSL https://github.com/APS-Conecta/gestion-aio/releases/latest/download/install.sh | sudo bash
#
# The release pipeline fills RELEASE_TAG and BUNDLE_SHA256 (aps/release/package.sh); the committed copy
# refuses to run. Order: check the host without changing it; verify the bundle's checksum; install
# Docker Engine when missing; unpack the bundle; start the mastercontainer as upstream's readme does,
# from the digest the bundle names; start the Provisionador and the inside-address timer and print the
# one-time link. A rerun once the mastercontainer exists changes nothing else: it makes sure the
# Provisionador and the timer run and prints a fresh link to the same wizard state; once the wizard
# closed at «Listo», the Provisionador stays stopped and the rerun prints the suite's address instead.
# Everything runs inside main, so a truncated download never runs half a script.
# GESTION_DOWNLOAD_URL is the tamper test's seam: it serves the release assets from elsewhere, under the
# same verification.
set -euo pipefail

RELEASE_TAG=""
BUNDLE_SHA256=""

# The hardware minimum, set here alone: each floor is the suite's peak use in the clean-machine acceptance run, every
# component on, plus 25%, rounded up to a whole GiB, core or GB (peaks.txt in the run's acceptance-hardware artifact):
# memory and disk over the whole run; cores in the busiest minute after «Listo» (the stages after 17-install in
# peaks.txt), as the install takes every core it gets and only runs longer on fewer. A run counts only if it is green
# with no OOM kill and almost no full memory stall; otherwise raise MIN_MEMORY_GIB (it sizes the acceptance VM) and
# measure again. Release run 41927261032 (4 cores, MemTotal 5.80 GiB) measured:
#   memory  4.50 GiB: 4.50 × 1.25 = 5.6, so 6 GiB.
#   cores   0.58 after «Listo», too thin a measure to go below the 2 the install keeps busy (1.97), so 2.
#   disk    17.1 GB, plus AIO's first nightly backup to the backup folder on the same disk: at most the 1.8 GB all the
#           suite's volumes hold after an install (docker system df, run 41907427126), and the 2 GB borg keeps free
#           (additional_free_space, Containers/borgbackup/backupscript.sh): (17.1 + 1.8 + 2) × 1.25 = 26.1, so 27 GB.
# Talk recording's threshold is these plus one recording (aps/provisionador/steps/components.py).
# Memory is MemTotal rounded up to a whole GiB, here as in the Provisionador and the acceptance checks: the kernel keeps
# part of the memory for itself (a 6 GiB machine reports about 5.8 GiB, about 5.35 GiB when kdump reserves 512 MiB), so
# a machine passes when it reports more than the floor less 1 GiB, and a 5 GiB machine is refused.
MIN_MEMORY_GIB=6
MIN_CPUS=2
MIN_DISK_GB=27

REPO_URL=https://github.com/APS-Conecta/gestion-aio
INSTALL_DIR=/opt/aps-conecta/gestion
MASTERCONTAINER=ghcr.io/aps-conecta/gestion-mastercontainer
WORK_DIR=""

die() { printf '\nError: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

apt_install() {
  export DEBIAN_FRONTEND=noninteractive
  { apt-get -qq -o DPkg::Lock::Timeout=300 update \
      && apt-get -qq -o DPkg::Lock::Timeout=300 install -y --no-install-recommends "$@"; } >/dev/null \
    || die "no se pudo instalar $* con apt-get. Revise el mensaje anterior y vuelva a ejecutar el instalador."
}

check_host() {
  local gaps=() arch memory_gib cpus disk_dir disk_gb system
  arch="$(uname -m)"
  [ "$arch" = x86_64 ] || gaps+=("la arquitectura del procesador es $arch; se requiere x86_64")
  memory_gib=$(( ($(awk '/^MemTotal:/ {print $2}' /proc/meminfo) + 1048575) / 1048576 ))
  [ "$memory_gib" -ge "$MIN_MEMORY_GIB" ] \
    || gaps+=("tiene $memory_gib GiB de memoria; se requieren al menos $MIN_MEMORY_GIB GiB")
  cpus="$(nproc)"
  [ "$cpus" -ge "$MIN_CPUS" ] || gaps+=("tiene $cpus núcleo(s) de CPU; se requieren al menos $MIN_CPUS")
  disk_dir=/var/lib/docker
  [ -d "$disk_dir" ] || disk_dir=/var/lib
  disk_gb="$(df --output=avail -B 1 "$disk_dir" | awk 'NR == 2 {print int($1 / 1e9)}')"
  [ "${disk_gb:-0}" -ge "$MIN_DISK_GB" ] \
    || gaps+=("tiene ${disk_gb:-0} GB libres en $disk_dir; se requieren al menos $MIN_DISK_GB GB")
  if ! command -v docker >/dev/null 2>&1 && [ -z "$(docker_repo)" ]; then
    # shellcheck source=/dev/null
    system="$(. "${OS_RELEASE:-/etc/os-release}" && echo "${PRETTY_NAME:-otro sistema}")"
    gaps+=("Docker no está instalado y el instalador solo lo instala en Ubuntu, Debian o un sistema basado en ellos (este servidor tiene $system)")
  fi
  [ "${#gaps[@]}" -eq 0 ] && return 0
  printf '\nEste servidor no cumple los requisitos de APS Conecta Gestión. No se modificó nada.\n' >&2
  printf '  - %s\n' "${gaps[@]}" >&2
  exit 1
}

fetch_and_verify() {
  local url="${GESTION_DOWNLOAD_URL:-$REPO_URL/releases/download/$RELEASE_TAG}" bundle="gestion-aio-$RELEASE_TAG.tar.gz"
  say "Descargando y verificando APS Conecta Gestión $RELEASE_TAG..."
  curl -fsSL --retry 3 -o "$WORK_DIR/$bundle" "$url/$bundle" \
    || die "no se pudo descargar $url/$bundle. Revise la conexión a internet del servidor y vuelva a ejecutar el instalador."
  [ "$(sha256sum "$WORK_DIR/$bundle" | cut -d ' ' -f 1)" = "$BUNDLE_SHA256" ] \
    || die "el paquete descargado no coincide con su suma de verificación. No se instaló nada; vuelva a ejecutar el instalador."
}

# Docker's packages for this system, as "<distribution> <suite>", or nothing when Docker publishes none for it: any
# Ubuntu- or Debian-based system takes the Ubuntu or Debian release it is built on, as a derivative's own codename (Linux
# Mint's, LMDE's…) has no packages. A derivative that names neither has its Debian release in /etc/debian_version.
docker_repo() (
  # shellcheck source=/dev/null
  . "${OS_RELEASE:-/etc/os-release}"
  if [ -n "${UBUNTU_CODENAME:-}" ]; then
    echo "ubuntu $UBUNTU_CODENAME"
  elif [ -n "${DEBIAN_CODENAME:-}" ]; then
    echo "debian $DEBIAN_CODENAME"
  elif [ "${ID:-}" = debian ] && [ -n "${VERSION_CODENAME:-}" ]; then
    echo "debian $VERSION_CODENAME"
  elif [ -r "${DEBIAN_VERSION:-/etc/debian_version}" ]; then
    case "$(cut -d . -f 1 "${DEBIAN_VERSION:-/etc/debian_version}")" in
      11) echo "debian bullseye" ;;
      12) echo "debian bookworm" ;;
      13) echo "debian trixie" ;;
    esac
  fi
)

install_docker() {
  local distribution suite
  read -r distribution suite <<< "$(docker_repo)"
  say "Instalando Docker Engine desde los paquetes oficiales de Docker..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL --retry 3 -o /etc/apt/keyrings/docker.asc "https://download.docker.com/linux/$distribution/gpg" \
    || die "no se pudo descargar la clave de los paquetes de Docker. Revise la conexión a internet del servidor."
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
    "$distribution" "$suite" > /etc/apt/sources.list.d/docker.sources
  apt_install docker-ce docker-ce-cli containerd.io
}

unpack() {
  rm -rf "$INSTALL_DIR.new"
  mkdir -p "$INSTALL_DIR.new"
  tar -xzf "$WORK_DIR/gestion-aio-$RELEASE_TAG.tar.gz" -C "$INSTALL_DIR.new"
  rm -rf "$INSTALL_DIR"
  mv "$INSTALL_DIR.new" "$INSTALL_DIR"
}

# The pinned digest runs under the release tag: upstream takes its channel from the image's tag. A test build's bundle
# carries Let's Encrypt's staging roots, which AIO's own folder of trusted CAs gives Nextcloud and Talk.
start_mastercontainer() {
  local pinned image="$MASTERCONTAINER:$RELEASE_TAG" trusted=()
  [ ! -d "$INSTALL_DIR/trusted-cacerts" ] || trusted=(--env "NEXTCLOUD_TRUSTED_CACERTS_DIR=$INSTALL_DIR/trusted-cacerts")
  pinned="$(grep -m 1 "^$MASTERCONTAINER@sha256:" "$INSTALL_DIR/images.lock")" \
    || die "el paquete no indica la imagen del contenedor maestro. Infórmelo a APS Conecta."
  say "Descargando el contenedor maestro..."
  docker pull --quiet "$pinned" >/dev/null \
    || die "no se pudo descargar el contenedor maestro. Revise la conexión a internet y vuelva a ejecutar el instalador."
  docker tag "$pinned" "$image"
  docker run --init --sig-proxy=false --name nextcloud-aio-mastercontainer --restart always --detach \
    --publish 80:80 --publish 8080:8080 --publish 8443:8443 \
    --volume nextcloud_aio_mastercontainer:/mnt/docker-aio-config \
    --volume /var/run/docker.sock:/var/run/docker.sock:ro "${trusted[@]}" \
    "$image" >/dev/null \
    || { docker rm -f nextcloud-aio-mastercontainer >/dev/null 2>&1
         die "no se pudo iniciar el contenedor maestro. Revise que los puertos 80, 8080 y 8443 estén libres y vuelva a ejecutar el instalador."; }
  say "APS Conecta Gestión $RELEASE_TAG está en marcha."
}

# The Provisionador is a systemd service on the host, with the host's Python: no third image of ours.
start_provisionador() {
  local missing=() c
  for c in python3 openssl qrencode; do command -v "$c" >/dev/null 2>&1 || missing+=("$c"); done
  if [ "${#missing[@]}" -gt 0 ]; then
    command -v apt-get >/dev/null 2>&1 \
      || die "instale ${missing[*]} con el gestor de paquetes de este sistema y vuelva a ejecutar el instalador."
    apt_install "${missing[@]}"
  fi
  cat > /etc/systemd/system/aps-provisionador.service <<EOF
[Unit]
Description=APS Conecta Gestión: Provisionador
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 $INSTALL_DIR/provisionador/provisionador.py serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now aps-provisionador.service >/dev/null 2>&1 \
    || die "no se pudo iniciar el Provisionador. Revise su registro con: journalctl -u aps-provisionador"
}

# The name «Dirección» claimed follows the server's inside address: every 10 minutes the host publishes it with
# the claim's root-only credential (until the claim, there is nothing to publish).
start_address_timer() {
  cat > /etc/systemd/system/aps-address.service <<EOF
[Unit]
Description=APS Conecta Gestión: publica la dirección interna del servidor
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $INSTALL_DIR/provisionador/provisionador.py publish
EOF
  cat > /etc/systemd/system/aps-address.timer <<EOF
[Unit]
Description=APS Conecta Gestión: publica la dirección interna del servidor cada 10 minutos

[Timer]
OnActiveSec=1min
OnUnitActiveSec=10min
AccuracySec=1s

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now aps-address.timer >/dev/null 2>&1 \
    || die "no se pudo activar la publicación de la dirección interna. Revise: systemctl status aps-address.timer"
}

# The link carries the wizard's secret: it goes to this console only.
print_link() {
  say ""
  python3 "$INSTALL_DIR/provisionador/provisionador.py" link
}

main() {
  [ "$(id -u)" -eq 0 ] \
    || die "ejecute el instalador como administrador: curl -fsSL $REPO_URL/releases/latest/download/install.sh | sudo bash"
  if [ -z "$RELEASE_TAG" ] || [ -z "$BUNDLE_SHA256" ]; then
    die "este archivo es la copia del repositorio y no trae la suma del paquete. Use el instalador publicado en $REPO_URL/releases"
  fi
  if command -v docker >/dev/null 2>&1 && docker container inspect nextcloud-aio-mastercontainer >/dev/null 2>&1; then
    start_provisionador
    start_address_timer
    print_link
    return 0
  fi
  check_host
  WORK_DIR="$(mktemp -d)"
  trap 'rm -rf "$WORK_DIR"' EXIT
  fetch_and_verify
  command -v docker >/dev/null 2>&1 || install_docker
  unpack
  start_mastercontainer
  start_provisionador
  start_address_timer
  print_link
}

main "$@"
