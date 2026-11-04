# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Background jobs run from AIO's cron, and Nextcloud's heavy daily jobs in a night window, as old gestion set them."""

from steps import configure, differences

NAME = "Tareas en segundo plano"
NEEDS = ("start",)
# The window opens at this hour (UTC) for four hours: 01:00 or 02:00 in continental Chile. Upstream's entrypoint sets
# it at every start from NEXTCLOUD_MAINTENANCE_WINDOW, which our thin Nextcloud image (aps/images/nextcloud) sets to it.
MAINTENANCE_WINDOW = 5
SETTINGS = {"system": {"maintenance_window_start": MAINTENANCE_WINDOW},
            "apps": {"core": {"backgroundjobs_mode": "cron"}}}


def run(context):
    return configure(context.suite, SETTINGS)


def check(context):
    return differences(context.suite, SETTINGS)
