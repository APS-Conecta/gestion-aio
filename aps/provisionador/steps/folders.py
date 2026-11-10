# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The group folders, in Groupfolders (a default store app, which the step ensures): one folder per unit and one per
team, each with the permissions teams.py declares for its groups and none for any other group."""

import json

import teams
from steps import apps

NAME = "Carpetas de equipo y permisos"
NEEDS = ("groups",)
MISSING = "Faltan estas carpetas de equipo: {}. Presione «Reintentar»; si vuelve a ocurrir, contacte a APS Conecta."
WRONG = ("Estas carpetas de equipo no tienen los permisos declarados: {}. Presione «Reintentar»; si vuelve a ocurrir, "
         "contacte a APS Conecta.")


def listed(suite):
    """Each group folder by its mount point: its id and each group's permissions."""
    found = json.loads(suite.occ("groupfolders:list", "--output=json"))
    return {f["mountPoint"]: {"id": f["id"], "groups": f.get("groups_list") or {}}
            for f in (found.values() if isinstance(found, dict) else found)}


def grant(suite, folder, declared):
    """Give each declared group its permissions in the folder, where it holds others, and no other group any."""
    for group, words in declared.items():
        if folder["groups"].get(group) != teams.bits(words):
            suite.occ("groupfolders:group", str(folder["id"]), group, *words)
    for group in folder["groups"].keys() - declared.keys():
        suite.occ("groupfolders:group", str(folder["id"]), group, "--delete")


def reasons(suite, declared):
    """What differs between the declared folders, by mount point with each group's permissions, and Groupfolders: the
    missing ones and those with other grants, as Spanish reasons."""
    held = listed(suite)
    missing = [mount for mount in declared if mount not in held]
    wrong = [mount for mount, grants in declared.items() if mount in held
             and held[mount]["groups"] != {group: teams.bits(words) for group, words in grants.items()}]
    return [reason.format(", ".join(mounts)) for reason, mounts in ((MISSING, missing), (WRONG, wrong)) if mounts]


def run(context):
    if failed := apps.ensure(context, "groupfolders"):
        return failed
    suite, held = context.suite, listed(context.suite)
    for mount, declared in teams.folders(context.teams).items():
        if mount not in held:
            held[mount] = {"id": int(suite.occ("groupfolders:create", mount)), "groups": {}}
        grant(suite, held[mount], declared)
    return check(context)


def check(context):
    return reasons(context.suite, teams.folders(context.teams))
