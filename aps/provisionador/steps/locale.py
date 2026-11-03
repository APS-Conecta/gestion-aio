# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spanish for everyone, with Chile's formats: core's one Spanish translation (es) whatever the browser asks for, and
the es_CL locale for dates, numbers and phone numbers."""

from steps import configure, differences

NAME = "Idioma y formato regional"
NEEDS = ("start",)
SETTINGS = {"system": {"default_language": "es", "force_language": "es", "default_locale": "es_CL",
                       "default_phone_region": "CL"}}


def run(context):
    configure(context.suite, SETTINGS)


def check(context):
    return differences(context.suite, SETTINGS)
