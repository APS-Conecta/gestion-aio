# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The groups: the staff-wide group, the four categories and the roles every establishment shares, by the ids and names
teams.py gives them."""

import json

import teams

NAME = "Grupos y equipos"
NEEDS = ("start",)
MISSING = "Nextcloud no tiene estos grupos: {}. Presione «Reintentar»; si vuelve a ocurrir, contacte a APS Conecta."


def members(suite):
    """Each group's members, by the group's id."""
    return json.loads(suite.occ("group:list", "--output=json")) or {}


def run(context):
    held = members(context.suite)
    for group, name in teams.groups(context.centre["type"]).items():
        if group not in held:
            context.suite.occ("group:add", f"--display-name={name}", group)
    return check(context)


def check(context):
    held = members(context.suite)
    missing = [name for group, name in teams.groups(context.centre["type"]).items() if group not in held]
    return [MISSING.format(", ".join(missing))] if missing else []
