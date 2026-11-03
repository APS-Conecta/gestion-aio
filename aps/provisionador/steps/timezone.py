# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The centre's timezone for Nextcloud's server-side clocks and its logs (both are UTC otherwise)."""

from steps import configure, differences

NAME = "Zona horaria"
NEEDS = ("start",)


def settings(context):
    zone = context.centre["timezone"]
    return {"system": {"default_timezone": zone, "logtimezone": zone}}


def run(context):
    return configure(context.suite, settings(context))


def check(context):
    return differences(context.suite, settings(context))
