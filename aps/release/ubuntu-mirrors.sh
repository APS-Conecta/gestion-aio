#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# A GitHub-hosted Ubuntu runner takes its packages through the mirror list /etc/apt/apt-mirrors.txt: Azure's mirror
# first, then Ubuntu's own archive and security servers (actions/runner-images, configure-apt-sources.sh). When a
# mirror's name does not resolve, apt fails instead of going on to the next one (Azure's on 2026-11-11, for hours), so
# the runner keeps only the mirrors whose names resolve, in their order, and says which it dropped. A runner whose
# mirrors all fail to resolve stops here, saying so.
set -euo pipefail
list=/etc/apt/apt-mirrors.txt
[ -f "$list" ] || exit 0
kept=()
while IFS= read -r mirror; do
  [ -n "$mirror" ] || continue
  host="$(printf '%s\n' "$mirror" | sed -E 's#^[a-z]+://([^/]+)/.*#\1#')"
  if getent hosts "$host" > /dev/null; then
    kept+=("$mirror")
  else
    echo "::warning title=Ubuntu mirror::$host does not resolve; apt goes on without it"
  fi
done < "$list"
[ "${#kept[@]}" -gt 0 ] || { echo "::error title=Ubuntu mirrors::none of the mirrors in $list resolves"; exit 1; }
printf '%s\n' "${kept[@]}" | sudo tee "$list" > /dev/null
