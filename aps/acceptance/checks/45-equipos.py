#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""«Equipos» in the installed suite, from the sectors and programs typed on «Equipos» (17-install): Nextcloud holds
every group the wizard declared and each group folder with exactly the declared groups' permissions; each cargo account
is enabled and in its groups, and the passwords file lists it. A sector's jefatura, as a staff member who never logged
in, sees Transversal's subfolders and the welcome page, where every login lands."""

import json
import shlex
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "provisionador")]
import teams  # noqa: E402
from browser import machine  # noqa: E402
from steps.passwords import FILE  # noqa: E402
from steps.welcome import APP, MOUNT  # noqa: E402

FOLDER = "/var/lib/aps-conecta/provisionador"  # the Provisionador's state and the passwords file


def occ(*args):
    return machine("sudo docker exec --user www-data nextcloud-aio-nextcloud php occ --no-ansi " + shlex.join(args))


declared = json.loads(machine(f"sudo cat {FOLDER}/state.json")).get("teams", [])
print("teams: " + ", ".join(team["name"] for team in declared))
if {team["kind"] for team in declared} != {"sector", "program"}:
    sys.exit("«Equipos» did not declare both a sector and a program")
wrong = [f"group {group} is missing" for group in teams.groups(declared)
         if group not in json.loads(occ("group:list", "--output=json"))]

held = {f["mountPoint"]: f.get("groups_list") or {} for f in json.loads(occ("groupfolders:list", "--output=json"))}
for mount, grants in teams.folders(declared).items():
    wanted = {group: teams.bits(words) for group, words in grants.items()}
    print(f"{mount}: {held.get(mount)}")
    if held.get(mount) != wanted:
        wrong.append(f"{mount} grants {held.get(mount)}, not {wanted}")

passwords = machine(f"sudo cat {FOLDER}/{FILE}")  # never printed
accounts = teams.cargo_accounts(declared)
for account in accounts:
    info = json.loads(occ("user:info", account["user"], "--output=json"))
    print(f"{account['user']}: {info['display_name']}, enabled {info['enabled']}, in {sorted(info['groups'])}")
    if not (info["enabled"] and set(account["groups"]) <= set(info["groups"])):
        wrong.append(f"{account['user']} is not enabled in {account['groups']}")
    if f"\n  Usuario: {account['user']}\n  Contraseña: " not in passwords:
        wrong.append(f"the passwords file does not list {account['user']}")

jefe = next(account["user"] for account in accounts if "role-jefe-sector-mais" in account["groups"])
listing = occ("info:file", f"/{jefe}/files/{teams.TRANSVERSAL}", "--children")
wrong += [f"{jefe} does not see {teams.TRANSVERSAL}/{sub}" for sub in teams.SUBFOLDERS if f"  - {sub}\n" not in listing]
occ("info:file", f"/{jefe}/files/{MOUNT}/es/home.json")  # fails the check unless the jefatura reads the welcome page
if occ("config:system:get", "defaultapp").strip() != APP:
    wrong.append(f"logins do not land on {APP}")
if wrong:
    sys.exit("\n".join(wrong))
print(f"the groups, the group folders with their permissions and the {len(accounts)} cargo accounts are what "
      f"«Equipos» declared; {jefe} sees {teams.TRANSVERSAL}'s subfolders and the welcome page")
