#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The static gates, the wizard-API tests and the name-service API tests, exactly as CI runs them on every PR.
# Run from anywhere inside the repo.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
python3 -I aps/gates/test_gates.py
python3 -I aps/gates/conformance.py
python3 -I aps/gates/deis_codes.py
python3 -I aps/gates/manifest.py
python3 -I aps/gates/brand.py
python3 -I aps/gates/release.py
python3 -I aps/gates/maintenance_window.py
python3 -I aps/gates/pins.py
python3 -I aps/provisionador/deis.py slugs > /dev/null
python3 -I -B -m unittest discover -s aps/provisionador
node --test aps/name-service/names.test.mjs
echo "gates: green"
