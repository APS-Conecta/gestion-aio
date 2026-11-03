# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cargo accounts teams.py declares, each in its groups, with a first password the passwords file lists. An account
that exists keeps its password."""

import teams
from steps import passwords
from steps.groups import members

NAME = "Cuentas de cargo"
NEEDS = ("groups",)
MISSING = ("Estas cuentas de cargo no existen o no están en sus grupos: {}. Presione «Reintentar»; si vuelve a "
           "ocurrir, contacte a APS Conecta.")


def run(context):
    suite, accounts = context.suite, teams.cargo_accounts(context.teams)
    first = passwords.first(context, {account["user"]: account["name"] for account in accounts})
    held = members(suite)
    for account in accounts:
        suite.add_user(account["user"], account["name"], first[account["user"]])
        for group in account["groups"]:
            if account["user"] not in held.get(group, ()):
                suite.occ("group:adduser", group, account["user"])
    return check(context)


def check(context):
    held = members(context.suite)
    missing = [account["user"] for account in teams.cargo_accounts(context.teams)
               if any(account["user"] not in held.get(group, ()) for group in account["groups"])]
    return [MISSING.format(", ".join(missing))] if missing else []
