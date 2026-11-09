# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cargo accounts teams.py declares, each in its groups, with a first password the passwords file lists, and each a
group admin of «Todo el personal», so it sets a temporary password for any staff member; no other account is a group
admin, and none of another group. An account that exists keeps its password."""

import teams
from steps import passwords
from steps.groups import members

NAME = "Cuentas de cargo"
NEEDS = ("groups",)
MISSING = ("Estas cuentas de cargo no existen o no están en sus grupos: {}. Presione «Reintentar»; si vuelve a "
           "ocurrir, contacte a APS Conecta.")
GROUP_ADMINS = ("Estas cuentas no son administradoras de grupo de «Todo el personal» o lo son de otros grupos: {}. "
                "Presione «Reintentar»; si vuelve a ocurrir, contacte a APS Conecta.")


def group_admin_changes(context):
    """Each account whose group-admin groups differ from the declared ones, by its username, with the declared ones:
    every cargo account administers «Todo el personal» alone, and no other account administers a group."""
    accounts = teams.cargo_accounts(context.teams, context.centre["type"])
    declared = {account["user"]: [teams.STAFF] for account in accounts}
    held = context.suite.group_admins()
    return {user: declared.get(user, []) for user in sorted(declared.keys() | held.keys())
            if sorted(declared.get(user, [])) != sorted(held.get(user, []))}


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
    return check(context)


def check(context):
    held = members(context.suite)
    missing = [account["user"] for account in teams.cargo_accounts(context.teams, context.centre["type"])
               if any(account["user"] not in held.get(group, ()) for group in account["groups"])]
    changes = group_admin_changes(context)
    return ([MISSING.format(", ".join(missing))] if missing else []) + (
        [GROUP_ADMINS.format(", ".join(changes))] if changes else [])
