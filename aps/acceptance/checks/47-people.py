#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The people the spreadsheet uploaded on «Equipos» (17-install) loaded, in the installed suite: each one not retired
(48-retire checks those) has an enabled account with their display name, email and groups, the staff-wide group and
their roles' categories among them, and the first administrator is in the admin group. Each logs in at the suite's
name, in a browser that trusts the staging roots alone, with the first password the passwords file lists for them; the
passwords are masked in the log, never printed."""

import json
import os
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "provisionador")]
import spreadsheet  # noqa: E402
from browser import chromium, first_password, occ, provisionador_file, provisionador_state, suite_name  # noqa: E402
from playwright.sync_api import Error  # noqa: E402
from steps.passwords import FILE  # noqa: E402
from steps.people import groups  # noqa: E402

WHOAMI = ("fetch('/ocs/v2.php/cloud/user?format=json', {headers: {'OCS-APIRequest': 'true'}})"
          ".then(r => r.json()).then(answer => answer.ocs.data.id)")

evidence = Path(os.environ["EVIDENCE"])
state = provisionador_state()
people, errors = spreadsheet.read(state.get("spreadsheet"), state.get("teams", []), state["centre"]["type"])
if errors or not people:
    sys.exit(f"«Equipos» loaded no people: {errors}")
people = [person for person in people if person["user"] not in state.get("retired", [])]
name = suite_name()
passwords = provisionador_file(FILE)  # never printed
wrong, first = [], {}
for person in people:
    info = json.loads(occ(f"user:info {person['user']} --output=json"))
    print(f"{person['user']}: {info['display_name']}, {info['email']}, enabled {info['enabled']}, "
          f"in {sorted(info['groups'])}")
    if ((info["display_name"], info["email"], info["enabled"]) != (person["name"], person["email"], True)
            or not groups(person) <= set(info["groups"])):
        wrong.append(f"{person['user']} is not {person['name']}, {person['email']}, enabled, in "
                     f"{sorted(groups(person))}")
    password = first_password(passwords, person["user"])
    if password:  # run.sh masked it once 17-install made it
        first[person["user"]] = password
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
