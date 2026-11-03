#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Assembles a release's assets in <out-dir>:
#   gestion-aio-<tag>.tar.gz          the bundle: VERSION, images.lock (every image the release runs, by digest,
#                                     the free domain's reverse proxy and local DNS included),
#                                     the Provisionador and the DEIS register
#   install.sh                        the stable-name bootstrap, with the tag and bundle checksum filled in
#   gestion-aio-<tag>-install.sh      the same bootstrap under its versioned name
#   SHA256SUMS                        checksums of the three
# Usage: package.sh <tag> <mastercontainer image@digest> <nextcloud image@digest> <out-dir>
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
[ "$#" -eq 4 ] || { echo "usage: $0 <tag> <mastercontainer-ref> <nextcloud-ref> <out-dir>" >&2; exit 2; }
tag=$1 mastercontainer=$2 nextcloud=$3 out=$4
bundle="gestion-aio-$tag.tar.gz"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
printf '%s\n' "$tag" > "$stage/VERSION"
{
  printf '%s\n' "$mastercontainer" "$nextcloud"
  jq -r '.aio_services_v1[] | select(.image_tag | startswith("sha256:")) | "\(.image)@\(.image_tag)"' php/containers.json \
    community-containers/caddy/caddy.json community-containers/dnsmasq/dnsmasq.json
} > "$stage/images.lock"

mkdir -p "$out"
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime=@0 --exclude='test_*.py' -czf "$out/$bundle" \
  -C "$stage" VERSION images.lock -C "$PWD/aps" provisionador deis
sha="$(sha256sum "$out/$bundle" | cut -d ' ' -f 1)"
sed -e "s|^RELEASE_TAG=\"\"$|RELEASE_TAG=\"$tag\"|" \
    -e "s|^BUNDLE_SHA256=\"\"$|BUNDLE_SHA256=\"$sha\"|" aps/bootstrap/install.sh > "$out/install.sh"
[ "$(grep -cE '^(RELEASE_TAG|BUNDLE_SHA256)="[^"]+"$' "$out/install.sh")" -eq 2 ] \
  || { echo "package: install.sh was not filled in" >&2; exit 1; }
cp "$out/install.sh" "$out/gestion-aio-$tag-install.sh"
(cd "$out" && sha256sum "$bundle" install.sh "gestion-aio-$tag-install.sh" > SHA256SUMS)
