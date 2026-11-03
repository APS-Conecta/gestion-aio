#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Fails unless the image <ref> (linux/amd64) carries <commit> as its org.opencontainers.image.revision.
# Usage: revision.sh <ref> <commit>
set -euo pipefail
[ "$#" -eq 2 ] || { echo "usage: $0 <ref> <commit>" >&2; exit 2; }
revision="$(docker buildx imagetools inspect "$1" --format '{{json .Image}}' \
  | jq -r 'if has("config") then . else .["linux/amd64"] end | .config.Labels["org.opencontainers.image.revision"] // ""')"
[ "$revision" = "$2" ] || { echo "::error::$1 was built from '${revision:-no revision}', not $2"; exit 1; }
echo "$1: revision $2"
