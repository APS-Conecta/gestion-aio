#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""«Retirar» in the installed suite: each person the operator retired on «Equipos» while the install ran (17-install),
after storing a file in their own files, cannot log in at the suite's name with the first password the passwords file
lists, in a browser that trusts the suite's roots alone. Nextcloud keeps their account, disabled and in none of the
groups the install manages, with that file; the passwords are masked in the log, never printed."""

import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "provisionador")]
import teams  # noqa: E402
from browser import chromium, machine  # noqa: E402
from steps.passwords import FILE  # noqa: E402

FOLDER = "/var/lib/aps-conecta/provisionador"  # the Provisionador's state and the passwords file
STORED = "Antes de retirarse.txt"  # as 17-install stores it, the username as its text
WHOAMI = ("fetch('/ocs/v2.php/cloud/user?format=json', {headers: {'OCS-APIRequest': 'true'}})"
          ".then(r => r.status)")


def occ(*args):
    return machine("sudo docker exec --user www-data nextcloud-aio-nextcloud php occ --no-ansi " + shlex.join(args))


evidence = Path(os.environ["EVIDENCE"])
state = json.loads(machine(f"sudo cat {FOLDER}/state.json"))
retired = state.get("retired") or sys.exit("«Retirar» retired nobody on «Equipos»")
managed = set(teams.groups(state["teams"], state["centre"]["type"]))
name = json.loads(machine("sudo docker exec nextcloud-aio-mastercontainer cat "
                          "/mnt/docker-aio-config/data/configuration.json"))["domain"]
passwords = machine(f"sudo cat {FOLDER}/{FILE}")  # never printed
wrong = []
with chromium() as browser:
    for user in retired:
        info = json.loads(occ("user:info", user, "--output=json"))
        print(f"{user}: enabled {info['enabled']}, in {sorted(info['groups'])}")
        if info["enabled"] or managed & set(info["groups"]):
            wrong.append(f"{user} is enabled or still in {sorted(managed & set(info['groups']))}")
        if occ("files:get", f"/{user}/files/{STORED}", "-") != user:
            wrong.append(f"{user}'s {STORED} does not hold what they stored")
        listed = re.search(rf"\n  Usuario: {re.escape(user)}\n  Contraseña: (\S+)\n", passwords)
        password = (listed or sys.exit(f"the passwords file does not list {user}"))[1]  # run.sh masked it
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(f"https://{name}/login")
        page.fill("#user", user)
        page.fill("#password", password)
        with page.expect_navigation():
            page.click("[data-login-form-submit]")
        page.screenshot(path=evidence / f"{user}.png")
        if page.evaluate(WHOAMI) != 401:
            wrong.append(f"{user} logs in at https://{name} with their first password")
        context.close()
if wrong:
    sys.exit("\n".join(wrong))
print(f"the {len(retired)} people «Retirar» retired cannot log in, and Nextcloud keeps their account, disabled and in "
      "no team, with their files")
