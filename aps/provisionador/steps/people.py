# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each person the people spreadsheet loads gets an account with their display name, email and groups: the staff-wide
group, outside the directorate the recovery group, their cargo's and its category's and, for the first administrator,
Nextcloud's admin group. A person retired with «Retirar» keeps their account, files and shares, disabled and in none of
the groups the install manages (teams.py's and admin), and so does a person a corrected spreadsheet drops once the
install made their account, until a later spreadsheet lists them again. A corrected spreadsheet takes a person out of
the managed groups it no longer gives them. Any other group, and any account the install did not make, stays as
Nextcloud has it. The first passwords of the accounts it makes are made once and the passwords file lists them under
their current name; an account that existed before keeps its own and is not listed.

The step applies the people as «Equipos» declared them when «Instalar» or «Reintentar» was pressed. Its part of the
end-of-install check compares them with what «Equipos» declares now (a change made since then waits for «Reintentar»)
and flags a disabled account the install made for a listed person «Retirar» did not retire; any other change an
administrator makes in Nextcloud it leaves alone."""

import json

import teams
from steps import passwords

NAME = "Cuentas del personal"
NEEDS = ("groups",)
ADMINS = "admin"  # Nextcloud's administrators
NEW = {"display_name": None, "email": None, "groups": (), "enabled": True}  # an account that does not exist yet
MISSING = ("Las cuentas de estas personas no quedaron como las declara «Equipos»: {}. Presione «Reintentar»; si "
           "vuelve a ocurrir, contacte a APS Conecta.")
WRONG = "La planilla del personal tiene errores. Corríjalos en «Equipos» y presione «Reintentar»."
CHANGED = "El personal cambió en «Equipos» después de aplicarse. Presione «Reintentar» para aplicar los cambios."
OWNED = True  # after «Listo» the Users page owns the people: an upgrade keeps this step green, never re-running it


def groups(person):
    """Every group the person's account is in."""
    cargo = person["cargo"]
    return {*teams.staff(cargo), *([cargo, teams.ROLES[cargo][1]] if cargo else []),
            *([ADMINS] if person["admin"] else [])}


def accounts(suite):
    """Every account, by its username: its display name, email, whether it is enabled and its groups, among others."""
    return json.loads(suite.occ("user:list", "--info", "--limit=0", "--output=json")) or {}


def made(folder, declared, centre_type):
    """The people whose account the install made, by username, with the name the passwords file shows: those it keeps a
    first password for, cargo accounts aside. An account made in Nextcloud is never among them."""
    cargo = {account["user"] for account in teams.cargo_accounts(declared, centre_type)}
    return {user: account["name"] for user, account in passwords.accounts(folder).items() if user not in cargo}


def differences(context, found):
    """By username, the occ commands that bring each account the step reconciles to what it declares: a listed person's
    display name, email and groups, and their account enabled if the install made it and «Retirar» did not retire them;
    the account of a person retired, or dropped from the spreadsheet once the install made it, disabled. A person the
    spreadsheet no longer lists is reconciled while their account exists."""
    managed, retired = {*teams.groups(context.centre["type"]), ADMINS}, set(context.retired)
    listed = {person["user"]: person for person in context.people}
    ours = set(made(context.folder, context.teams, context.centre["type"]))
    leaving = retired | ours
    needed = {}
    for user in [*listed, *sorted(leaving & (found.keys() - listed.keys()))]:
        account, person, commands = found.get(user, NEW), listed.get(user), []
        if person:
            commands += [("user:setting", "--", user, "settings", key, value)  # «--»: a name may start with «-»
                         for key, value in (("display_name", person["name"]), ("email", person["email"]))
                         if account[key] != value]
        gone = user in retired or not person
        wanted = set() if gone else groups(person)
        held = set(account["groups"])
        commands += [("group:removeuser", group, user) for group in sorted(held & (managed - wanted))]
        commands += [("group:adduser", group, user) for group in sorted(wanted - held)]
        if gone and account["enabled"]:
            commands.append(("user:disable", user))
        elif not gone and not account["enabled"] and user in ours:
            commands.append(("user:enable", user))
        needed[user] = commands
    return needed


def run(context):
    suite, people = context.suite, context.people
    if not people:
        return [WRONG]
    found, kept = accounts(suite), passwords.accounts(context.folder)
    first = passwords.first(context, {person["user"]: person["name"] for person in people
                                      if person["user"] not in found or person["user"] in kept})
    created = [person for person in people if person["user"] not in found]
    for person in created:
        suite.add_user(person["user"], person["name"], first[person["user"]])
    for commands in differences(context, accounts(suite) if created else found).values():
        for command in commands:
            suite.occ(*command)
    missing = [user for user, commands in differences(context, accounts(suite)).items() if commands]
    return [MISSING.format(", ".join(missing))] if missing else check(context)


def check(context):
    people, retired = context.declared()
    if not people:
        return [WRONG]
    if (people, retired) != (context.people, context.retired):
        return [CHANGED]
    disabled = [user for user, commands in differences(context, accounts(context.suite)).items()
                if ("user:enable", user) in commands]
    return [MISSING.format(", ".join(disabled))] if disabled else []
