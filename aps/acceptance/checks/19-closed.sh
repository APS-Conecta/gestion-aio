#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The wizard closed at «Listo»: the Provisionador stopped and its port answers no more, and the passwords file it
# handed over stays on the machine, readable by root alone. 90-rerun checks what the command prints then.
set -euo pipefail
service="$("$MACHINE" 'systemctl is-active aps-provisionador || true')"
echo "aps-provisionador: $service"
[ "$service" = inactive ] || { echo "the Provisionador still runs after «Listo»" >&2; exit 1; }
if "$MACHINE" 'timeout 5 bash -c "exec 3<> /dev/tcp/127.0.0.1/8081"' 2> /dev/null; then
  echo "the wizard's port still answers after «Listo»" >&2
  exit 1
fi
mode="$("$MACHINE" "sudo stat -c '%U %a' /var/lib/aps-conecta/provisionador/contrasenas.txt")"
[ "$mode" = "root 600" ] || { echo "the passwords file is kept with owner and mode $mode, not root 600" >&2; exit 1; }
echo "the wizard closed at «Listo»; the passwords file stays on the machine, root-only"
