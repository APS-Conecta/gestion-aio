#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What staff do in the group folders the install made (45-equipos checks their grants), over WebDAV at the suite's
name with the first password the passwords file lists for each: a person the spreadsheet loaded (17-install) who holds
no directorate cargo writes and deletes a file in a sector's, a program's and a unit's folder and in the Intranet, and
finds neither Dirección nor Subdirección in their files; the Secretario/a de Dirección sees both and writes and deletes
a file in each. The passwords are masked in the log, never printed."""

import json
import sys
import unicodedata
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from urllib.parse import quote, unquote

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "provisionador")]
import teams  # noqa: E402
from browser import as_person, first_password, occ, provisionador_file, provisionador_state, suite_name  # noqa: E402
from steps.passwords import FILE  # noqa: E402
from steps.welcome import MOUNT  # noqa: E402

NOTE = "Prueba de carpetas.txt"
SECRETARY = "role-secretario-direccion"

state = provisionador_state()
declared = state.get("teams", [])
suite = f"https://{suite_name()}"
passwords = provisionador_file(FILE)  # never printed
members = json.loads(occ("group:list --output=json"))
cargo = {account["user"] for account in teams.cargo_accounts(declared, state["centre"]["type"])}
directorate = {user for role in teams.DIRECTORATE for user in members.get(role, ())}
staff = sorted(set(members.get(teams.STAFF, ())) - directorate - cargo)
secretaries = sorted(set(members.get(SECRETARY, ())) - cargo)
shared = [next((team["folder"] for team in declared if team["kind"] == kind), None) for kind in ("sector", "program")]
if not (staff and secretaries and all(shared)):
    sys.exit("the install loaded no staff member outside the directorate, no Secretario/a de Dirección or no sector and "
             "program")


def url(user, path):
    return f"{suite}/remote.php/dav/files/{user}/{quote(path)}"


def password(user):
    return first_password(passwords, user) or sys.exit(f"the passwords file does not list {user}")


def writes(user, folder):
    """Whether `user` stores a file in `folder` and deletes it again."""
    stored = as_person(user, password(user), "PUT", url(user, f"{folder}/{NOTE}"), user.encode())[0]
    deleted = as_person(user, password(user), "DELETE", url(user, f"{folder}/{NOTE}"))[0]
    print(f"{user} in {folder}: PUT {stored}, DELETE {deleted}")
    return stored in (201, 204) and deleted == 204


def units(user):
    """The folders `user` sees in Unidades."""
    status, body = as_person(user, password(user), "PROPFIND", url(user, "Unidades/"), headers={"Depth": "1"})
    if status != 207:
        sys.exit(f"{user} does not list Unidades: HTTP {status}")
    paths = [unquote(href.text).rstrip("/") for href in ElementTree.fromstring(body).iter("{DAV:}href")]
    seen = {unicodedata.normalize("NFC", path.rsplit("/", 1)[-1]) for path in paths[1:]}
    print(f"{user} sees in Unidades: {sorted(seen)}")
    return seen


wrong = []
person, secretary = staff[0], secretaries[0]
wrong += [f"{person} does not write in {folder}" for folder in (*shared, f"Unidades/{teams.UNITS[0]}", MOUNT)
          if not writes(person, folder)]
seen = units(person)
if seen & set(teams.PRIVATE) or not set(teams.UNITS) <= seen:
    wrong.append(f"{person} does not see exactly the shared units in Unidades")
if not set(teams.PRIVATE) <= units(secretary):
    wrong.append(f"{secretary} does not see {' and '.join(teams.PRIVATE)}")
wrong += [f"{secretary} does not write in Unidades/{unit}" for unit in teams.PRIVATE
          if not writes(secretary, f"Unidades/{unit}")]
if wrong:
    sys.exit("\n".join(wrong))
print(f"{person} writes in a sector, a program, a unit and the {MOUNT} and does not see "
      f"{' or '.join(teams.PRIVATE)}; {secretary} sees and writes in both")
