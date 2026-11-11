#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Assembles a release's assets in <out-dir>:
#   gestion-aio-<tag>.tar.gz          the bundle: VERSION, images.lock (every image the release runs, by digest),
#                                     services.json, the mastercontainer's start, the Provisionador, the DEIS register,
#                                     the brand and the theme;
#                                     a test build's also trusted-cacerts/, Let's Encrypt's staging roots, one per file
#   install.sh                        the stable-name bootstrap, with the tag and bundle checksum filled in
#   gestion-aio-<tag>-install.sh      the same bootstrap under its versioned name
#   SHA256SUMS                        checksums of the three
# Usage: package.sh <tag> <mastercontainer image@digest> <nextcloud image@digest> <caddy image@digest> <out-dir>
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
[ "$#" -eq 5 ] || { echo "usage: $0 <tag> <mastercontainer-ref> <nextcloud-ref> <caddy-ref> <out-dir>" >&2; exit 2; }
tag=$1 mastercontainer=$2 nextcloud=$3 caddy=$4 out=$5
bundle="gestion-aio-$tag.tar.gz"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
printf '%s\n' "$tag" > "$stage/VERSION"
{
  printf '%s\n' "$mastercontainer" "$nextcloud" "$caddy"
  jq -r '.aio_services_v1[] | select(.image_tag | startswith("sha256:")) | "\(.image)@\(.image_tag)"' php/containers.json
} > "$stage/images.lock"
# The one choice between test builds and releases: a test build claims names under ci.gestion.apsconecta.cl from
# the name service's CI deployment and gets its certificates from Let's Encrypt's staging service, whose roots the
# server then trusts (install.sh hands trusted-cacerts/ to AIO, which mounts it in Nextcloud and Talk).
trusted=()
case "$tag" in
  v0.0.0-ci.*) url=https://gestion-nombres-ci.apsconecta.workers.dev suffix=ci.gestion.apsconecta.cl
               acme_ca=https://acme-staging-v02.api.letsencrypt.org/directory trusted=(trusted-cacerts)
               mkdir "$stage/trusted-cacerts"
               awk -v dir="$stage/trusted-cacerts" '
                 /BEGIN CERTIFICATE/ { n++; out = dir "/letsencrypt-staging-" n ".crt" }
                 out { print > out }
                 /END CERTIFICATE/ { close(out); out = "" }' aps/acceptance/letsencrypt-staging-roots.pem ;;
  *) url=https://gestion-nombres.apsconecta.workers.dev suffix=gestion.apsconecta.cl
     acme_ca=https://acme-v02.api.letsencrypt.org/directory ;;
esac
jq -n --arg url "$url" --arg suffix "$suffix" --arg acme_ca "$acme_ca" '{$url, $suffix, $acme_ca}' > "$stage/services.json"

mkdir -p "$out"
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime=@0 --exclude='test_*.py' -czf "$out/$bundle" \
  -C "$stage" VERSION images.lock services.json "${trusted[@]}" \
  -C "$PWD/aps" bootstrap/mastercontainer.sh provisionador deis brand theme
sha="$(sha256sum "$out/$bundle" | cut -d ' ' -f 1)"
sed -e "s|^RELEASE_TAG=\"\"$|RELEASE_TAG=\"$tag\"|" \
    -e "s|^BUNDLE_SHA256=\"\"$|BUNDLE_SHA256=\"$sha\"|" aps/bootstrap/install.sh > "$out/install.sh"
[ "$(grep -cE '^(RELEASE_TAG|BUNDLE_SHA256)="[^"]+"$' "$out/install.sh")" -eq 2 ] \
  || { echo "package: install.sh was not filled in" >&2; exit 1; }
cp "$out/install.sh" "$out/gestion-aio-$tag-install.sh"
(cd "$out" && sha256sum "$bundle" install.sh "gestion-aio-$tag-install.sh" > SHA256SUMS)
