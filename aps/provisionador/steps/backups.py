# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""AIO's own nightly backup, to the backup folder on this server."""

NAME = "Copias de seguridad"
NEEDS = ()
BACKUP_FOLDER = "/var/backups/aps-conecta"  # AIO's nightly backup keeps its repository here
BACKUP_TIME = "06:00"  # UTC, as AIO's clock runs: 02:00 or 03:00 in continental Chile


def run(context):
    context.suite.back_up(BACKUP_FOLDER, BACKUP_TIME)
