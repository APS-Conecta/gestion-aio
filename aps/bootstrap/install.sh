#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The bootstrap. The operator runs it as the one command:
#   curl -fsSL https://github.com/APS-Conecta/gestion-aio/releases/latest/download/install.sh | sudo bash
#
# The release pipeline fills RELEASE_TAG, BUNDLE_SHA256 and PUBLIC_KEY (aps/release/package.sh); the
# committed copy refuses to run. Order: check the host without changing it; install minisign when
# missing; verify the bundle's signature and checksum; install Docker Engine when missing; unpack the
# bundle; start the mastercontainer as upstream's readme does, from the digest the bundle names.
# Everything runs inside main, so a truncated download never runs half a script.
# GESTION_DOWNLOAD_URL is the tamper test's seam: it serves the release assets from elsewhere, under the
# same verification.
set -euo pipefail

RELEASE_TAG=""
BUNDLE_SHA256=""
PUBLIC_KEY=""

# The hardware minimum: the old installer's memory and disk floors, two CPU cores.
MIN_MEMORY_GIB=6
MIN_CPUS=2
MIN_DISK_GB=20

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
  local gaps=() arch memory_gib cpus disk_dir disk_gb
  arch="$(uname -m)"
  [ "$arch" = x86_64 ] || gaps+=("la arquitectura del procesador es $arch; se requiere x86_64")
  memory_gib=$(( ($(awk '/^MemTotal:/ {print $2}' /proc/meminfo) + 524288) / 1048576 ))
  [ "$memory_gib" -ge "$MIN_MEMORY_GIB" ] \
    || gaps+=("tiene $memory_gib GiB de memoria; se requieren al menos $MIN_MEMORY_GIB GiB")
  cpus="$(nproc)"
  [ "$cpus" -ge "$MIN_CPUS" ] || gaps+=("tiene $cpus núcleo(s) de CPU; se requieren al menos $MIN_CPUS")
  disk_dir=/var/lib/docker
  [ -d "$disk_dir" ] || disk_dir=/var/lib
  disk_gb="$(df --output=avail -B 1 "$disk_dir" | awk 'NR == 2 {print int($1 / 1e9)}')"
  [ "${disk_gb:-0}" -ge "$MIN_DISK_GB" ] \
    || gaps+=("tiene ${disk_gb:-0} GB libres en $disk_dir; se requieren al menos $MIN_DISK_GB GB")
  if ! command -v docker >/dev/null 2>&1; then
    # shellcheck source=/dev/null
    . /etc/os-release
    case "${ID:-}" in
      ubuntu | debian) ;;
      *) gaps+=("Docker no está instalado y el instalador solo lo instala en Ubuntu o Debian (este servidor tiene ${PRETTY_NAME:-otro sistema})") ;;
    esac
  fi
  [ "${#gaps[@]}" -eq 0 ] && return 0
  printf '\nEste servidor no cumple los requisitos de APS Conecta Gestión. No se modificó nada.\n' >&2
  printf '  - %s\n' "${gaps[@]}" >&2
  exit 1
}

fetch_and_verify() {
  local url="${GESTION_DOWNLOAD_URL:-$REPO_URL/releases/download/$RELEASE_TAG}" bundle="gestion-aio-$RELEASE_TAG.tar.gz" f
  say "Descargando y verificando APS Conecta Gestión $RELEASE_TAG..."
  for f in "$bundle" SHA256SUMS SHA256SUMS.minisig; do
    curl -fsSL --retry 3 -o "$WORK_DIR/$f" "$url/$f" \
      || die "no se pudo descargar $url/$f. Revise la conexión a internet del servidor y vuelva a ejecutar el instalador."
  done
  minisign -Vq -P "$PUBLIC_KEY" -m "$WORK_DIR/SHA256SUMS" -x "$WORK_DIR/SHA256SUMS.minisig" \
    || die "la firma de la versión $RELEASE_TAG no es válida. No se instaló nada; descargue el instalador nuevamente desde $REPO_URL/releases"
  if ! grep -qxF "$BUNDLE_SHA256  $bundle" "$WORK_DIR/SHA256SUMS" \
    || [ "$(sha256sum "$WORK_DIR/$bundle" | cut -d ' ' -f 1)" != "$BUNDLE_SHA256" ]; then
    die "el paquete descargado no coincide con su suma de verificación. No se instaló nada; vuelva a ejecutar el instalador."
  fi
}

install_docker() {
  say "Instalando Docker Engine desde los paquetes oficiales de Docker..."
  # shellcheck source=/dev/null
  . /etc/os-release
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL --retry 3 -o /etc/apt/keyrings/docker.asc "https://download.docker.com/linux/$ID/gpg" \
    || die "no se pudo descargar la clave de los paquetes de Docker. Revise la conexión a internet del servidor."
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
    "$ID" "${UBUNTU_CODENAME:-$VERSION_CODENAME}" > /etc/apt/sources.list.d/docker.sources
  apt_install docker-ce docker-ce-cli containerd.io
}

unpack() {
  rm -rf "$INSTALL_DIR.new"
  mkdir -p "$INSTALL_DIR.new"
  tar -xzf "$WORK_DIR/gestion-aio-$RELEASE_TAG.tar.gz" -C "$INSTALL_DIR.new"
  rm -rf "$INSTALL_DIR"
  mv "$INSTALL_DIR.new" "$INSTALL_DIR"
}

print_address() {
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  say "Abra https://${ip:-<dirección IP del servidor>}:8080 en su navegador para continuar."
}

# The pinned digest runs under the release tag: upstream takes its channel from the image's tag.
start_mastercontainer() {
  local pinned image="$MASTERCONTAINER:$RELEASE_TAG"
  pinned="$(grep -m 1 "^$MASTERCONTAINER@sha256:" "$INSTALL_DIR/images.lock")" \
    || die "el paquete no indica la imagen del contenedor maestro. Infórmelo a APS Conecta."
  say "Descargando el contenedor maestro..."
  docker pull --quiet "$pinned" >/dev/null \
    || die "no se pudo descargar el contenedor maestro. Revise la conexión a internet y vuelva a ejecutar el instalador."
  docker tag "$pinned" "$image"
  docker run --init --sig-proxy=false --name nextcloud-aio-mastercontainer --restart always --detach \
    --publish 80:80 --publish 8080:8080 --publish 8443:8443 \
    --volume nextcloud_aio_mastercontainer:/mnt/docker-aio-config \
    --volume /var/run/docker.sock:/var/run/docker.sock:ro \
    "$image" >/dev/null \
    || { docker rm -f nextcloud-aio-mastercontainer >/dev/null 2>&1
         die "no se pudo iniciar el contenedor maestro. Revise que los puertos 80, 8080 y 8443 estén libres y vuelva a ejecutar el instalador."; }
  say "APS Conecta Gestión $RELEASE_TAG está en marcha."
  print_address
}

main() {
  [ "$(id -u)" -eq 0 ] \
    || die "ejecute el instalador como administrador: curl -fsSL $REPO_URL/releases/latest/download/install.sh | sudo bash"
  if [ -z "$RELEASE_TAG" ] || [ -z "$BUNDLE_SHA256" ] || [ -z "$PUBLIC_KEY" ]; then
    die "este archivo es la copia del repositorio y no trae la suma del paquete. Use el instalador publicado en $REPO_URL/releases"
  fi
  if command -v docker >/dev/null 2>&1 && docker container inspect nextcloud-aio-mastercontainer >/dev/null 2>&1; then
    say "APS Conecta Gestión ya está instalada en este servidor; no se cambió nada."
    print_address
    return 0
  fi
  check_host
  command -v minisign >/dev/null 2>&1 || apt_install minisign
  WORK_DIR="$(mktemp -d)"
  trap 'rm -rf "$WORK_DIR"' EXIT
  fetch_and_verify
  command -v docker >/dev/null 2>&1 || install_docker
  unpack
  start_mastercontainer
}

main "$@"
