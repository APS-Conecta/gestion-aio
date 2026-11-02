#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Builds one of our images from the checked-out tree for linux/amd64 and pushes it by digest; publishes
# it under <tag> only if it carries the release <commit> as its revision. Prints <image>@<digest>.
# Usage: build-image.sh <image> <dockerfile> <context> <tag> <commit>
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
[ "$#" -eq 5 ] || { echo "usage: $0 <image> <dockerfile> <context> <tag> <commit>" >&2; exit 2; }
image=$1 dockerfile=$2 context=$3 tag=$4 commit=$5
metadata="$(mktemp)"
docker buildx build --platform linux/amd64 --file "$dockerfile" \
  --label "org.opencontainers.image.revision=$(git rev-parse HEAD)" \
  --label "org.opencontainers.image.source=https://github.com/APS-Conecta/gestion-aio" \
  --label "org.opencontainers.image.version=$tag" \
  --output "type=image,name=$image,push-by-digest=true,name-canonical=true,push=true" \
  --metadata-file "$metadata" "$context" >&2
digest="$(jq -r '."containerimage.digest"' "$metadata")"
aps/release/revision.sh "$image@$digest" "$commit" >&2
docker buildx imagetools create --tag "$image:$tag" "$image@$digest" >&2
echo "$image@$digest"
