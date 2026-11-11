# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cargo accounts teams.py declares, each in its groups, with a first password the passwords file lists, and each a
group admin of the groups teams.py has it administer: the director's and subdirector's of «Todo el personal», so they
set a temporary password for any staff member, the jefaturas' of the recovery group alone, so the directorate's
passwords are out of their reach; no other account is a group admin, but what an upgrade keeps of the group admins
administrators made after «Listo» (`steps.kept`). An account that exists keeps its password."""

import steps
import teams
from steps import passwords
from steps.groups import members

NAME = "Cuentas de cargo"
NEEDS = ("groups",)
MISSING = ("Estas cuentas de cargo no existen o no están en sus grupos: {}. Presione «Reintentar»; si vuelve a "
           "ocurrir, contacte a APS Conecta.")
GROUP_ADMINS = ("Los administradores de grupo de estas cuentas no quedaron como los declara «Equipos» (solo las "
                "cuentas de cargo: las de director y subdirector, de «Todo el personal»; las demás, solo de «Personal "
                "fuera del equipo directivo»): {}. Presione «Reintentar»; si vuelve a ocurrir, contacte a APS "
                "Conecta.")
KIND = "group_admins"  # in the record: the groups each cargo account was made a group admin of, by its username


def declared_admins(context):
    """The groups each cargo account administers, by its username, as teams.py declares them."""
    return {account["user"]: account["administers"]
            for account in teams.cargo_accounts(context.teams, context.centre["type"])}


def group_admin_changes(context):
    """Each account whose group-admin groups differ from the wanted ones, by its username, with the wanted ones: every
    cargo account administers the groups teams.py declares for it, and no account administers another group but what
    an upgrade keeps (`steps.kept`)."""
    declared, held = declared_admins(context), context.suite.group_admins()
    kept = steps.kept(context, KIND, held)
    wanted = {user: sorted({*declared.get(user, []), *kept.get(user, [])}) for user in declared.keys() | held.keys()}
    return {user: groups for user, groups in sorted(wanted.items()) if groups != sorted(held.get(user, []))}


def run(context):
    suite, accounts = context.suite, teams.cargo_accounts(context.teams, context.centre["type"])
    first = passwords.first(context, {account["user"]: account["name"] for account in accounts})
    held = members(suite)
    for account in accounts:
        suite.add_user(account["user"], account["name"], first[account["user"]])
        for group in account["groups"]:
            if account["user"] not in held.get(group, ()):
                suite.occ("group:adduser", group, account["user"])
    for user, groups in group_admin_changes(context).items():
        suite.group_admin(user, groups)
    return steps.record(context, KIND, declared_admins(context), check(context))


def check(context):
    held = members(context.suite)
    missing = [account["user"] for account in teams.cargo_accounts(context.teams, context.centre["type"])
               if any(account["user"] not in held.get(group, ()) for group in account["groups"])]
    changes = group_admin_changes(context)
    return ([MISSING.format(", ".join(missing))] if missing else []) + (
        [GROUP_ADMINS.format(", ".join(changes))] if changes else [])
