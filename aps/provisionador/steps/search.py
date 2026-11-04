# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Full-text search as AIO sets it up with its switch: its Elasticsearch container and the fulltextsearch,
fulltextsearch_elasticsearch and files_fulltextsearch apps; the files provider is the Suite manifest's fork, which the
step ensures (aps/keep-list.txt says why). The step builds the index with vanilla's own command, as AIO's first start
does, and vanilla's index job, which AIO's cron runs, keeps it current."""

from steps import apps

NAME = "Índice de búsqueda de texto completo"
NEEDS = ("start",)
APPS = ("fulltextsearch", "fulltextsearch_elasticsearch", "files_fulltextsearch")
OFF = ("AIO desactivó la búsqueda de texto completo porque su contenedor no respondió a tiempo cuando se inició "
       "Nextcloud. Contacte a APS Conecta.")


def run(context):
    if failed := apps.ensure(context, "files_fulltextsearch"):
        return failed
    off = check(context)
    if not off:
        context.suite.occ("fulltextsearch:index", "--no-readline")
    return off


def check(context):
    return [OFF] if set(APPS) - apps.enabled(context.suite) else []
