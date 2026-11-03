# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Office as in vanilla Nextcloud: Nextcloud's own Office entry, on for every staff member, creates and lists the
documents, and Euro-Office, whose connector AIO's Nextcloud container installs with the component, opens them by
default. Staff's browsers reach Euro-Office at the suite's name; Nextcloud and Euro-Office reach each other inside the
suite's network, as AIO wires Collabora (apache's plain internal listener), so they never depend on its certificate."""

import json

from steps import configure, differences, until

NAME = "Office con Euro-Office"
NEEDS = ("start",)
OFFICE, EUROOFFICE = "office", "eurooffice"  # Nextcloud's Office entry, and Euro-Office's connector
WAIT = 5 * 60  # seconds: Euro-Office may still be starting once AIO reports its container running
ODF = json.dumps(dict.fromkeys(("odt", "ods", "odp"), True), separators=(",", ":"))
SETTINGS = {"apps": {OFFICE: {"enabled": "yes"}, EUROOFFICE: {
    "enabled": "yes",
    # ODF opens and edits (lossy) in Euro-Office, as in old gestion; OOXML keeps the connector's default.
    "defFormats": ODF, "editFormats": ODF,
    "DocumentServerInternalUrl": "http://nextcloud-aio-eurooffice/",
    "StorageUrl": "http://nextcloud-aio-apache.nextcloud-aio:23973/"}}}
SILENT = ("Euro-Office no responde a Nextcloud. Presione «Reintentar» en unos minutos; si vuelve a ocurrir, contacte "
          "a APS Conecta.")


def run(context):
    for app in (OFFICE, EUROOFFICE):
        context.suite.occ("app:enable", app)
    wrong = configure(context.suite, SETTINGS)
    if wrong:
        return wrong
    return [] if until(context.suite.eurooffice_answers, bool, WAIT) else [SILENT]


def check(context):
    return differences(context.suite, SETTINGS)
