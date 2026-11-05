# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Background jobs run from AIO's cron, as old gestion set them, and Nextcloud's heavy daily jobs in a night window that
closes before the nightly backup."""

from steps import configure, differences

NAME = "Tareas en segundo plano"
NEEDS = ("start",)
# The window opens at this hour (UTC) for four hours: 21:00 or 22:00 in continental Chile, closing an hour before AIO's
# nightly backup (steps/backups.py), which stops the suite. Upstream's entrypoint sets it at every start from
# NEXTCLOUD_MAINTENANCE_WINDOW, which our thin Nextcloud image (aps/images/nextcloud) sets to it; the maintenance-window
# gate (aps/gates/maintenance_window.py) holds the two equal.
MAINTENANCE_WINDOW = 1
SETTINGS = {"system": {"maintenance_window_start": MAINTENANCE_WINDOW},
            "apps": {"core": {"backgroundjobs_mode": "cron"}}}


def run(context):
    return configure(context.suite, SETTINGS)


def check(context):
    return differences(context.suite, SETTINGS)
