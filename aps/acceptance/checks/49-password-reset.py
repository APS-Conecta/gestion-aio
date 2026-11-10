#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A temporary password, in the installed suite: a sector's jefatura, signed in with the first password the passwords
file lists, sets one for a staff member through Nextcloud's provisioning API, which «Cuentas» also uses. The person logs
in with it at the suite's name, in a browser that trusts the suite's roots alone, and replaces it in «Configuración
personal → Seguridad»; their own client then signs in with the new one. Nextcloud's audit log holds the change with the
jefatura as the acting account. The jefatura is refused when setting an administrator's password, and the audit log
holds no such change. Every password stays in this process's memory and in request bodies: never printed, never on a
command line."""

import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "provisionador")]
import teams  # noqa: E402
from browser import NEXTCLOUD, as_person, chromium, first_password, log_in, machine, occ  # noqa: E402
from browser import provisionador_file, provisionador_state, suite_name  # noqa: E402
from playwright.sync_api import Error  # noqa: E402
from steps.passwords import FILE  # noqa: E402

CHANGED = 'Password of user "{}" has been changed'  # admin_audit's entry for a password change

evidence = Path(os.environ["EVIDENCE"])
state = provisionador_state()
accounts = [account["user"] for account in teams.cargo_accounts(state.get("teams", []), state["centre"]["type"])]
positions = {user for user, _, _ in teams.POSITIONS}
jefe = next((user for user in accounts if user not in positions), None) or sys.exit("«Equipos» declared no sector")
listed = json.loads(occ("group:list --output=json"))
administrators = listed.get("admin", [])
staff = sorted(set(listed.get(teams.STAFF, [])) - set(administrators) - set(accounts))
person = staff[0] if staff else sys.exit("«Todo el personal» has no staff member but administrators and cargo accounts")
suite = f"https://{suite_name()}"
password = first_password(provisionador_file(FILE), jefe) or sys.exit(f"the passwords file does not list {jefe}")
temporary, replaced = secrets.token_urlsafe(18), secrets.token_urlsafe(18)  # never printed


def reset(user, value):
    """What Nextcloud answers the jefatura setting `user`'s password to `value` through the provisioning API: the HTTP
    status and the OCS status."""
    status, body = as_person(jefe, password, "PUT", f"{suite}/ocs/v2.php/cloud/users/{quote(user)}?format=json",
                             urlencode({"key": "password", "value": value}).encode(),
                             {"Content-Type": "application/x-www-form-urlencoded"})
    return status, json.loads(body or "{}").get("ocs", {}).get("meta", {})


status, meta = reset(person, temporary)
print(f"{jefe} sets a temporary password for {person}: HTTP {status}, {meta}")
if status != 200:
    sys.exit(f"{jefe} cannot set {person}'s password")

with chromium() as browser:
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    try:
        log_in(page, suite, person, temporary)
        page.goto(f"{suite}/settings/user/security")
        page.fill("input[name=oldpassword]", temporary)
        page.fill("input[name=newpassword]", replaced)
        with page.expect_response(lambda r: r.url.endswith("/settings/personal/changepassword")) as answer:
            page.press("input[name=newpassword]", "Enter")
        changed = answer.value.json()
    except Error as error:
        page.screenshot(path=evidence / "security.png")
        sys.exit(f"{person} does not log in with the temporary password and replace it: "
                 f"{error.message.splitlines()[0]}")
    page.screenshot(path=evidence / "security.png")
print(f"{person} logs in with it and replaces it in «Configuración personal → Seguridad»: {changed}")
if changed.get("status") != "success":
    sys.exit(f"{person} cannot replace the temporary password")
if as_person(person, replaced, "GET", f"{suite}/ocs/v2.php/cloud/user?format=json")[0] != 200:
    sys.exit(f"{person}'s own client does not sign in with the new password")

wrong = []
for administrator in administrators:
    status, meta = reset(administrator, secrets.token_urlsafe(18))
    print(f"{jefe} sets {administrator}'s password: HTTP {status}, {meta}")
    if status == 200:
        wrong.append(f"{jefe} sets the password of {administrator}, an administrator")

audit = occ("config:system:get logfile_audit").strip()
entries = [json.loads(line) for line in machine(f"{NEXTCLOUD} cat {audit}").splitlines() if "Password of user" in line]
changes = {(entry.get("user"), entry.get("message")) for entry in entries}
for entry in entries:
    print(f"audit: {entry.get('time')} {entry.get('user')}: {entry.get('message')}")
if (jefe, CHANGED.format(person)) not in changes:
    wrong.append(f"the audit log holds no change of {person}'s password by {jefe}")
wrong += [f"the audit log holds a change of {administrator}'s password by {jefe}" for administrator in administrators
          if (jefe, CHANGED.format(administrator)) in changes]
if wrong:
    sys.exit("\n".join(wrong))
print(f"{jefe} set a temporary password for {person}, who logged in with it and replaced it; the audit log names "
      f"{jefe} as the actor; {jefe} cannot set the password of {', '.join(administrators)}")
