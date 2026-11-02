#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The static gates, exactly as CI runs them on every PR. Run from anywhere inside the repo.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
python3 -I aps/gates/test_gates.py
python3 -I aps/gates/conformance.py
python3 -I aps/gates/deis_codes.py
echo "gates: green"
