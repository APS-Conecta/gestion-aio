#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The mastercontainer's start, the only code that runs it: the bootstrap's install and upgrade, and the Provisionador's
# «Almacenamiento» step, which moves the staff's files to another disk before AIO first creates Nextcloud's container.
# It runs from the unpacked bundle, whose release it starts: its tag (VERSION) and its pinned digest in images.lock. The
# pinned digest runs under the release tag: upstream takes its channel from the image's tag. A test build's bundle
# carries Let's Encrypt's staging roots, which AIO's own folder of trusted CAs gives Nextcloud and Talk. A host's render
# devices go to the mastercontainer, as upstream's readme says, so AIO passes them to the containers that use a GPU:
# «IA local»'s Ollama among them. The staff's files folder the Provisionador stored is AIO's NEXTCLOUD_DATADIR; with
# none stored, AIO keeps the files in its own volume. The mastercontainer this start replaces is removed first; its
# configuration volume stays, so an upgrade keeps everything AIO configured (a clean machine has none to keep, and with
# `fresh` the volume a failed start made goes too, or the rerun would refuse it as another installation's).
# Usage: mastercontainer.sh fresh|keep
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MASTERCONTAINER=ghcr.io/aps-conecta/gestion-mastercontainer
DRI=/dev/dri  # the host's render devices, an AMD or Intel GPU's among them
FILES=/var/lib/aps-conecta/files-folder  # the staff's files folder, as the Provisionador stores it (suite.py)

die() { printf '\nError: %s\n' "$*" >&2; exit 1; }

keep="$1" trusted=() devices=() files=()
pinned="$(grep -m 1 "^$MASTERCONTAINER@sha256:" "$INSTALL_DIR/images.lock")" \
  || die "el paquete no indica la imagen del contenedor maestro. Infórmelo a APS Conecta."
image="$MASTERCONTAINER:$(cat "$INSTALL_DIR/VERSION")"
[ ! -d "$INSTALL_DIR/trusted-cacerts" ] || trusted=(--env "NEXTCLOUD_TRUSTED_CACERTS_DIR=$INSTALL_DIR/trusted-cacerts")
[ ! -d "$DRI" ] || devices=(--device "$DRI")
[ ! -s "$FILES" ] || files=(--env "NEXTCLOUD_DATADIR=$(cat "$FILES")")
echo "Descargando el contenedor maestro..."
docker pull --quiet "$pinned" >/dev/null \
  || die "no se pudo descargar el contenedor maestro. Revise la conexión a internet y vuelva a ejecutar el instalador."
docker tag "$pinned" "$image"
docker rm -f nextcloud-aio-mastercontainer >/dev/null 2>&1 || true
docker run --init --sig-proxy=false --name nextcloud-aio-mastercontainer --restart always --detach \
  --publish 80:80 --publish 8080:8080 --publish 8443:8443 \
  --volume nextcloud_aio_mastercontainer:/mnt/docker-aio-config \
  --volume /var/run/docker.sock:/var/run/docker.sock:ro "${trusted[@]}" "${devices[@]}" "${files[@]}" \
  "$image" >/dev/null \
  || { docker rm -f nextcloud-aio-mastercontainer >/dev/null 2>&1
       [ "$keep" = keep ] || docker volume rm nextcloud_aio_mastercontainer >/dev/null 2>&1 || true
       die "no se pudo iniciar el contenedor maestro. Revise que los puertos 80, 8080 y 8443 estén libres y vuelva a ejecutar el instalador."; }
