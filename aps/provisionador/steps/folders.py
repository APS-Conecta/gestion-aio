# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The group folders, in Groupfolders (a default store app, which the step ensures): one folder per unit and one per
team, each with the permissions teams.py declares for its groups and none for any other group; an upgrade keeps the
grants administrators added after «Listo» (`steps.kept`)."""

import json

import steps
import teams
from steps import apps

NAME = "Carpetas de equipo y permisos"
NEEDS = ("groups",)
MISSING = "Faltan estas carpetas de equipo: {}. Presione «Reintentar»; si vuelve a ocurrir, contacte a APS Conecta."
WRONG = ("Estas carpetas de equipo no tienen los permisos declarados: {}. Presione «Reintentar»; si vuelve a ocurrir, "
         "contacte a APS Conecta.")
KIND = "folder_grants"  # in the record: the groups the step granted permissions in each folder, by its mount point


def listed(suite):
    """Each group folder by its mount point: its id and each group's permissions."""
    found = json.loads(suite.occ("groupfolders:list", "--output=json"))
    return {f["mountPoint"]: {"id": f["id"], "groups": f.get("groups_list") or {}}
            for f in (found.values() if isinstance(found, dict) else found)}


def wanted(context, kind, held, declared, *, app_groups=()):
    """Each declared folder's grants once the step is done, by mount point and group: each declared group's
    permissions, and each other group's that an upgrade keeps (`steps.kept`, the step's record under `kind`) as `held`
    (as `listed` answers it) has them, never one of `app_groups`, the groups an app grants a folder itself."""
    kept = steps.kept(context, kind, {mount: [group for group in held[mount]["groups"] if group not in app_groups]
                                      for mount in declared if mount in held})
    return {mount: {**{group: held[mount]["groups"][group] for group in kept.get(mount, [])},
                    **{group: teams.bits(words) for group, words in grants.items()}}
            for mount, grants in declared.items()}


def grant(context, kind, held, declared, *, app_groups=()):
    """Give each declared folder's groups, {mount point: {group: permissions}}, their permissions where `held` (as
    `listed` answers it) has others, and no group the step does not want any (`wanted`)."""
    target = wanted(context, kind, held, declared, app_groups=app_groups)
    for mount, grants in declared.items():
        folder = held[mount]
        for group, words in grants.items():
            if folder["groups"].get(group) != teams.bits(words):
                context.suite.occ("groupfolders:group", str(folder["id"]), group, *words)
        for group in folder["groups"].keys() - target[mount].keys():
            context.suite.occ("groupfolders:group", str(folder["id"]), group, "--delete")


def reasons(context, kind, declared, *, app_groups=()):
    """What differs between the declared folders, by mount point with each group's permissions, and Groupfolders: the
    missing ones and those with other grants than the step wants (`wanted`), as Spanish reasons."""
    held = listed(context.suite)
    target = wanted(context, kind, held, declared, app_groups=app_groups)
    missing = [mount for mount in declared if mount not in held]
    wrong = [mount for mount in declared if mount in held and held[mount]["groups"] != target[mount]]
    return [reason.format(", ".join(mounts)) for reason, mounts in ((MISSING, missing), (WRONG, wrong)) if mounts]


def run(context):
    if failed := apps.ensure(context, "groupfolders"):
        return failed
    suite, held, declared = context.suite, listed(context.suite), teams.folders(context.teams)
    for mount in declared:
        if mount not in held:
            held[mount] = {"id": int(suite.occ("groupfolders:create", mount)), "groups": {}}
    grant(context, KIND, held, declared)
    return steps.record(context, KIND, declared, check(context))


def check(context):
    return reasons(context, KIND, teams.folders(context.teams))
