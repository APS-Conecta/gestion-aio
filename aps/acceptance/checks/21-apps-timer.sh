#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The store's apps update nightly after AIO's backup window: the host's aps-apps.timer is enabled for 06:15 UTC and
# its oneshot service runs provisionador.py update-apps, which runs Nextcloud's own update of the store's apps — run
# once by hand here, with the suite up, it answers nothing and changes nothing.
set -euo pipefail
enabled="$("$MACHINE" 'systemctl is-enabled aps-apps.timer')"
[ "$enabled" = enabled ] || { echo "aps-apps.timer is $enabled, not enabled" >&2; exit 1; }
runs_at="$("$MACHINE" 'systemctl show aps-apps.timer --property=NextElapseUSecRealtime --value')"
grep -q ' 06:15:00 ' <<< " $runs_at " || { echo "aps-apps.timer next runs at $runs_at, not 06:15 UTC" >&2; exit 1; }
exec_start="$("$MACHINE" 'systemctl show aps-apps.service --property=ExecStart --value')"
grep -q 'provisionador.py update-apps' <<< "$exec_start" \
  || { echo "aps-apps.service runs $exec_start, not the Provisionador's update-apps" >&2; exit 1; }
"$MACHINE" 'sudo python3 /opt/aps-conecta/gestion/provisionador/provisionador.py update-apps' \
  || { echo "update-apps failed with the suite up" >&2; exit 1; }
echo "the store's apps update nightly (aps-apps.timer at 06:15 UTC, enabled; update-apps answers with the suite up)"
