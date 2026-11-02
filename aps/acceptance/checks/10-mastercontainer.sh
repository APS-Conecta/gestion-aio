#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The mastercontainer runs the image the release pins, under the release's tag.
set -euo pipefail
image=ghcr.io/aps-conecta/gestion-mastercontainer
version="$("$MACHINE" cat /opt/aps-conecta/gestion/VERSION)"
[ "$version" = "$TAG" ] || { echo "the machine holds the bundle of $version, not $TAG" >&2; exit 1; }
lock="$("$MACHINE" cat /opt/aps-conecta/gestion/images.lock)"
pinned="$(grep -m 1 "^$image@sha256:" <<< "$lock")"
read -r running runs id <<< "$("$MACHINE" "sudo docker inspect -f '{{.State.Running}} {{.Config.Image}} {{.Image}}' nextcloud-aio-mastercontainer")"
expected="$("$MACHINE" "sudo docker image inspect -f '{{.Id}}' $pinned")"
echo "nextcloud-aio-mastercontainer: running $running, $runs, $id; $TAG pins $pinned, $expected"
[[ "$running" = true && "$runs" = "$image:$TAG" && "$id" = "$expected" ]] \
  || { echo "the mastercontainer does not run $pinned as $image:$TAG" >&2; exit 1; }
