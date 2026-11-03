#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# AIO's page takes the brand from aps/brand before the mastercontainer is built: the brand token file and its fonts
# beside the page's stylesheet, the favicon, and the page's SVG art rendered with the tokens' values.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
cp aps/brand/tokens.css aps/brand/*.woff2 aps/brand/*.woff2.license php/public/
cp aps/brand/favicon.svg php/public/img/
python3 -I aps/provisionador/brand.py php/public/img/favicon.svg php/public/img/nextcloud-logo.svg
