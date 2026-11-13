#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""«Equipos» in the installed suite, from the sectors and programs typed on «Equipos» (17-install): Nextcloud holds
every group the wizard declared with its name, one per cargo among them, and each group folder, the Intranet among them,
with exactly the declared groups' permissions, the Intranet's still after the check runs IntraVox's setup again, as each
IntraVox update and Nextcloud's repair run it (only a red leaves the suite changed); each cargo account is enabled and
in its groups, and the passwords file lists it. A sector's jefatura, as a staff member who never logged in, sees the
welcome page, where every login lands (46-folders checks what staff do in the folders)."""

import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "provisionador")]
import teams  # noqa: E402
from browser import first_password, occ, provisionador_file, provisionador_state  # noqa: E402
from steps.passwords import FILE  # noqa: E402
from steps.welcome import APP, GRANTS, MOUNT, SETUP  # noqa: E402


def folder_grants():
    """Each group folder's groups with their permissions, by mount point."""
    return {f["mountPoint"]: f.get("groups_list") or {} for f in json.loads(occ("groupfolders:list --output=json"))}


state = provisionador_state()
declared, centre_type = state.get("teams", []), state["centre"]["type"]
print("teams: " + ", ".join(team["name"] for team in declared))
if {team["kind"] for team in declared} != {"sector", "program"}:
    sys.exit("«Equipos» did not declare both a sector and a program")
groups = {group: info["displayName"] for group, info in json.loads(occ("group:list --info --output=json")).items()}
print(f"{len(teams.ROLES)} cargo groups: " + ", ".join(groups.get(role, f"{role} missing") for role in teams.ROLES))
wrong = [f"group {group} is {groups.get(group, 'missing')}, not «{name}»"
         for group, name in teams.groups(centre_type).items() if groups.get(group) != name]

held = folder_grants()
for mount, grants in {**teams.folders(declared), **GRANTS}.items():
    wanted = {group: teams.bits(words) for group, words in grants.items()}
    print(f"{mount}: {held.get(mount)}")
    if held.get(mount) != wanted:
        wrong.append(f"{mount} grants {held.get(mount)}, not {wanted}")
occ(" ".join(SETUP))
again = folder_grants().get(MOUNT)
print(f"{MOUNT} after IntraVox's setup ran again: {again}")
if again != held.get(MOUNT):
    wrong.append(f"IntraVox's setup changed the {MOUNT} grants to {again}")

passwords = provisionador_file(FILE)  # never printed
accounts = teams.cargo_accounts(declared, centre_type)
for account in accounts:
    info = json.loads(occ(f"user:info {account['user']} --output=json"))
    print(f"{account['user']}: {info['display_name']}, enabled {info['enabled']}, in {sorted(info['groups'])}")
    if not (info["enabled"] and set(account["groups"]) <= set(info["groups"])):
        wrong.append(f"{account['user']} is not enabled in {account['groups']}")
    if not first_password(passwords, account["user"]):
        wrong.append(f"the passwords file does not list {account['user']}")

jefe = next(account["user"] for account in accounts if "role-jefe-sector-mais" in account["groups"])
occ(f"info:file /{jefe}/files/{MOUNT}/es/home.json")  # fails the check unless the jefatura reads the welcome page
if occ("config:system:get defaultapp").strip() != APP:
    wrong.append(f"logins do not land on {APP}")
if wrong:
    sys.exit("\n".join(wrong))
print(f"the groups with their names, the {len(teams.ROLES)} cargos' among them, the group folders with their "
      f"permissions and the {len(accounts)} cargo accounts are what «Equipos» declared; {jefe} sees the welcome page")
