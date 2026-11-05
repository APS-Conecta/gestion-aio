#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Suite manifest's apps in the installed suite: every default app is enabled, and so is each optional app the
operator ticked on «Revisar», while no other optional app is installed. Each own and fork app runs the version of the
App release whose tag the lock file records."""

import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "provisionador")]
from browser import occ, provisionador_file, provisionador_state  # noqa: E402
from steps.apps import LOCK, MANIFEST, OPTIONAL  # noqa: E402

listed = json.loads(occ("app:list --output=json"))
ticked = set(provisionador_state().get("optional", []))
lock = json.loads(provisionador_file(LOCK))
wrong = []
for app in MANIFEST:
    wanted = app["id"] not in OPTIONAL or app["id"] in ticked
    version, tag = listed["enabled"].get(app["id"]), lock.get(app["id"])
    print(f"{app['id']} ({app['source']}{', optional' if app['id'] in OPTIONAL else ''}): "
          f"{'enabled ' + version if version else 'not enabled'}{', lock ' + tag if tag else ''}")
    if wanted and not version:
        wrong.append(f"{app['id']} is not enabled")
    if not wanted and (app["id"] in listed["enabled"] or app["id"] in listed["disabled"]):
        wrong.append(f"{app['id']} was not ticked on «Revisar» but is installed")
    if wanted and app["source"] != "store" and version and tag != f"v{version}":
        wrong.append(f"{app['id']} runs {version}, not the App release the lock file records ({tag})")
if wrong:
    sys.exit("\n".join(wrong))
print("every default app and each ticked optional app is enabled, own and fork apps at their locked tags")
