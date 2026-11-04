#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The people the spreadsheet uploaded on «Equipos» (17-install) loaded, in the installed suite: each has an enabled
account with their display name, email and groups, the staff-wide group and their roles' categories among them, and the
first administrator is in the admin group. Each logs in at the suite's name, in a browser that trusts the staging roots
alone, with the first password the passwords file lists for them; the passwords are masked in the log, never printed."""

import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "provisionador")]
import spreadsheet  # noqa: E402
from browser import chromium, machine  # noqa: E402
from playwright.sync_api import Error  # noqa: E402
from steps.passwords import FILE  # noqa: E402
from steps.people import groups  # noqa: E402

FOLDER = "/var/lib/aps-conecta/provisionador"  # the Provisionador's state and the passwords file
WHOAMI = ("fetch('/ocs/v2.php/cloud/user?format=json', {headers: {'OCS-APIRequest': 'true'}})"
          ".then(r => r.json()).then(answer => answer.ocs.data.id)")


def occ(*args):
    return machine("sudo docker exec --user www-data nextcloud-aio-nextcloud php occ --no-ansi " + shlex.join(args))


evidence = Path(os.environ["EVIDENCE"])
state = json.loads(machine(f"sudo cat {FOLDER}/state.json"))
people, errors = spreadsheet.read(state.get("spreadsheet"), state.get("teams", []), state["centre"]["type"])
if errors or not people:
    sys.exit(f"«Equipos» loaded no people: {errors}")
name = json.loads(machine("sudo docker exec nextcloud-aio-mastercontainer cat "
                          "/mnt/docker-aio-config/data/configuration.json"))["domain"]
passwords = machine(f"sudo cat {FOLDER}/{FILE}")  # never printed
wrong, first = [], {}
for person in people:
    info = json.loads(occ("user:info", person["user"], "--output=json"))
    print(f"{person['user']}: {info['display_name']}, {info['email']}, enabled {info['enabled']}, "
          f"in {sorted(info['groups'])}")
    if ((info["display_name"], info["email"], info["enabled"]) != (person["name"], person["email"], True)
            or not groups(person) <= set(info["groups"])):
        wrong.append(f"{person['user']} is not {person['name']}, {person['email']}, enabled, in "
                     f"{sorted(groups(person))}")
    listed = re.search(rf"\n  Usuario: {re.escape(person['user'])}\n  Contraseña: (\S+)\n", passwords)
    if listed:
        print(f"::add-mask::{listed[1]}", flush=True)
        first[person["user"]] = listed[1]
    else:
        wrong.append(f"the passwords file does not list {person['user']}")
if wrong:
    sys.exit("\n".join(wrong))

with chromium() as browser:
    for user, password in first.items():
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        try:
            page.goto(f"https://{name}/login")
            page.fill("#user", user)
            page.fill("#password", password)
            page.click("[data-login-form-submit]")
            page.wait_for_url(lambda url: "/login" not in url, timeout=60_000)
            signed_in = page.evaluate(WHOAMI)
        except Error as error:
            page.screenshot(path=evidence / f"{user}.png")
            sys.exit(f"{user} does not log in at https://{name} with their first password: "
                     f"{error.message.splitlines()[0]}")
        page.screenshot(path=evidence / f"{user}.png")
        print(f"{user} logs in at https://{name} and lands on {page.url}")
        if signed_in != user:
            sys.exit(f"logging in as {user} opened a session for {signed_in}")
        context.close()
print(f"the {len(people)} people the spreadsheet loaded have their accounts and log in with the first password the "
      "passwords file lists")
