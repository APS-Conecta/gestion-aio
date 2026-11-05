#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Runs the one command, or <command> (such as the wizard's `link`), on the machine as the operator does and prints
# what it printed without the one-time link's secret: the secret is masked in the log (::add-mask::) before anything
# is printed, and the QR code, which encodes it, is left out. The whole output goes to <file>, outside the evidence,
# for the checks.
# Usage: one-command.sh <file> [<command>]
set -euo pipefail
rc=0
timeout 15m "$MACHINE" "${2:-$COMMAND}" > "$1" 2>&1 || rc=$?
{ grep -o 'https://[^[:space:]]*#[A-Za-z0-9_-]*' "$1" || true; } | sed 's/.*#/::add-mask::/'
grep -v -F -e $'\e' -e '▀' -e '▄' -e '█' "$1" || true
exit "$rc"
