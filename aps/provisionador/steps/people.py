# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each person the people spreadsheet loads gets an account with their display name, email and groups: the listed ones,
the staff-wide group, each listed role's category and, for the first administrator, Nextcloud's admin group. Their
first passwords are made once and the passwords file lists them; an account that exists keeps its own."""

import json

import teams
from steps import passwords

NAME = "Cuentas del personal"
NEEDS = ("groups",)
ADMINS = "admin"  # Nextcloud's administrators
MISSING = ("Estas personas no tienen su cuenta con su nombre, su correo y sus grupos: {}. Presione «Reintentar»; si "
           "vuelve a ocurrir, contacte a APS Conecta.")


def groups(person):
    """Every group the person's account is in."""
    listed = set(person["groups"])
    return {teams.STAFF, *listed, *(teams.ROLES[group][1] for group in listed if group in teams.ROLES),
            *([ADMINS] if person["admin"] else [])}


def accounts(suite):
    """Every account, by its username: its display name, email and groups, among others."""
    return json.loads(suite.occ("user:list", "--info", "--limit=0", "--output=json")) or {}


def held(person, account):
    """Whether the account holds the person's display name, email and groups."""
    return bool(account) and (account["display_name"], account["email"]) == (person["name"], person["email"]) and (
        groups(person) <= set(account["groups"]))


def run(context):
    suite, people = context.suite, context.people
    first = passwords.first(context, {person["user"]: person["name"] for person in people})
    found = accounts(suite)
    for person in people:
        user = person["user"]
        if user not in found:
            suite.add_user(user, person["name"], first[user])
        account = found.get(user, {"display_name": person["name"], "email": "", "groups": []})
        for key, wanted in (("display_name", person["name"]), ("email", person["email"])):
            if account[key] != wanted:
                suite.occ("user:setting", user, "settings", key, wanted)
        for group in sorted(groups(person) - set(account["groups"])):
            suite.occ("group:adduser", group, user)
    return check(context)


def check(context):
    found = accounts(context.suite)
    missing = [person["user"] for person in context.people if not held(person, found.get(person["user"]))]
    return [MISSING.format(", ".join(missing))] if missing else []
