# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""AIO's own nightly backup, to the backup destination the operator chose on «Almacenamiento»: the backup folder on this
server, or the aps-conecta/copias folder of the backup disk, which «Almacenamiento» mounted for good. The destination is
tested first: a file written there and removed at once, so «Listo» never comes with a backup that cannot be written.
AIO's automatic updates are on too: AIO's nightly job (Containers/mastercontainer/daily-backup.sh) then keeps the
containers on the release's pins and updates the store's apps, which our host timer (install.sh) also does every
night."""

from steps.storage import BACKUP_GONE, backup_disk, point

NAME = "Copias de seguridad"
NEEDS = ("storage",)
BACKUP_FOLDER = "/var/backups/aps-conecta"  # the backup folder: the backup destination on the system disk
BACKUP_TIME = "06:00"  # UTC, as AIO's clock runs: 02:00 or 03:00 in continental Chile
COPIES = "aps-conecta/copias"  # the backup's folder on a backup disk, beside the staff's files folder
WRITTEN = "Copia de seguridad: se pudo escribir en {}."
DIFFERS = ("AIO guarda la copia de seguridad en {} a las {} UTC, no en {} a las {} UTC. Presione «Reintentar»; si vuelve "
           "a ocurrir, contacte a APS Conecta.")


def destination(context):
    """The folder AIO's nightly backup goes to; raises LookupError when the backup disk is gone."""
    disk = backup_disk(context)
    return f"{point(disk)}/{COPIES}" if disk else BACKUP_FOLDER


def run(context):
    try:
        folder = destination(context)
    except LookupError:
        return [BACKUP_GONE]
    context.suite.writable(folder)
    context.log(WRITTEN.format(folder))
    context.suite.back_up(folder, BACKUP_TIME)
    return None


def check(context):
    try:
        folder = destination(context)
    except LookupError:
        return [BACKUP_GONE]
    held = context.suite.backup_settings()
    return [DIFFERS.format(*held, folder, BACKUP_TIME)] if held != (folder, BACKUP_TIME) else None
