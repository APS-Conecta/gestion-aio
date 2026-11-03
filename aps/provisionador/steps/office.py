# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Office as in vanilla Nextcloud: Nextcloud's own Office entry, on for every staff member, creates and lists the
documents, and Euro-Office, whose connector AIO's Nextcloud container installs with the component, opens them by
default. Staff's browsers reach Euro-Office at the suite's name; Nextcloud and Euro-Office reach each other inside the
suite's network, as AIO wires Collabora (apache's plain internal listener), so they never depend on its certificate."""

import json

from steps import configure, differences, until
from suite import Unreachable

NAME = "Office con Euro-Office"
NEEDS = ("start",)
OFFICE, EUROOFFICE = "office", "eurooffice"  # Nextcloud's Office entry, and Euro-Office's connector
WAIT = 5 * 60  # seconds: Euro-Office may still be starting once AIO reports its container running
SETTINGS = {"apps": {OFFICE: {"enabled": "yes"}, EUROOFFICE: {
    "enabled": "yes",
    # Euro-Office opens what the Office entry creates; every other format keeps the connector's default.
    "defFormats": json.dumps({"docx": True, "xlsx": True, "pptx": True}, separators=(",", ":")),
    "DocumentServerInternalUrl": "http://nextcloud-aio-eurooffice/",
    "StorageUrl": "http://nextcloud-aio-apache.nextcloud-aio:23973/"}}}
SILENT = ("Euro-Office no responde a Nextcloud. Presione «Reintentar» en unos minutos; si vuelve a ocurrir, contacte "
          "a APS Conecta.")


def answers(suite):
    """Whether Euro-Office answers Nextcloud, by the connector's own check: Euro-Office converts a file it fetches from
    Nextcloud."""
    try:
        suite.occ("eurooffice:documentserver", "--check")
    except Unreachable:
        return False
    return True


def run(context):
    for app in (OFFICE, EUROOFFICE):
        context.suite.occ("app:enable", app)
    wrong = configure(context.suite, SETTINGS)
    return wrong or ([] if until(lambda: answers(context.suite), bool, WAIT) else [SILENT])


def check(context):
    return differences(context.suite, SETTINGS)
