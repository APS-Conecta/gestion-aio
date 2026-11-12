# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""«Almacenamiento»: the staff's files on the files disk the operator chose, and the backup disk mounted when the
backup goes to one. Before AIO first creates Nextcloud's container, each chosen disk is mounted for good under
/mnt/aps-conecta/<uuid> (a disk already mounted at boot keeps its mount point) and AIO keeps the staff's files in the
files disk's aps-conecta/archivos folder: its NEXTCLOUD_DATADIR, which the bootstrap's mastercontainer start passes from
then on. Files on the system disk, and an install that stored no files disk, stay in AIO's own volume. Nothing is
formatted, partitioned or deleted. At an upgrade only the files disk counts: after «Listo» the backup destination is
AIO's page's, which may have moved it off the backup disk."""

from suite import MOUNTS, OWN_VOLUME

NAME = "Almacenamiento"
NEEDS = ()
FOLDER = "aps-conecta/archivos"  # the staff's files folder on the files disk
GONE = ("El disco elegido para los archivos del personal ya no está en el servidor. Conéctelo y presione "
        "«Reintentar».")
BACKUP_GONE = ("El disco elegido para la copia de seguridad ya no está en el servidor. Conéctelo y presione "
               "«Reintentar», o elija otro destino en «Almacenamiento».")
MOUNTED = "Disco {} montado en {}."
NOT_MOUNTED = ("El disco {} ya no queda montado en {} en cada arranque del servidor. Presione «Reintentar»; si vuelve a "
               "ocurrir, contacte a APS Conecta.")
ELSEWHERE = ("AIO guarda los archivos del personal en {}, no en {}. Presione «Reintentar»; si vuelve a ocurrir, "
             "contacte a APS Conecta.")


def chosen(context, disk_id):
    """A disk chosen on «Almacenamiento», by its id, as the suite port reads it now; None when none was chosen or it is
    the system disk. Raises LookupError when that disk is gone."""
    if disk_id is None:
        return None
    disk = next((found for found in context.suite.disks() if found["id"] == disk_id), None)
    if disk is None:
        raise LookupError(disk_id)
    return None if disk["system"] else disk


def files_disk(context):
    return chosen(context, context.files)


def backup_disk(context):
    return chosen(context, (context.backups or {}).get("disk"))


def point(disk):
    """Where the disk is, or is to be, mounted for good."""
    return disk["mounted"] or f"{MOUNTS}/{disk['id']}"


def disk_name(disk):
    return f"{disk['model'] or 'Disco'} ({disk['device']})"


def chosen_disks(context):
    """The files disk and the backup disk, each None when not on a disk of its own, the backup disk always at an
    upgrade; raises LookupError naming GONE or BACKUP_GONE when one is gone."""
    try:
        files = files_disk(context)
    except LookupError as gone:
        raise LookupError(GONE) from gone
    try:
        return files, None if context.upgrade else backup_disk(context)
    except LookupError as gone:
        raise LookupError(BACKUP_GONE) from gone


def run(context):
    try:
        files, backup = chosen_disks(context)
    except LookupError as gone:
        return [str(gone)]
    points = {}
    for disk in (files, backup):
        if disk and disk["id"] not in points:
            points[disk["id"]] = disk["mounted"] if disk["mounted"] and disk["at_boot"] else context.suite.mount(disk)
            if points[disk["id"]] != disk["mounted"]:
                context.log(MOUNTED.format(disk_name(disk), points[disk["id"]]))
    if files:
        context.suite.place_files(f"{points[files['id']]}/{FOLDER}")
    return None


def check(context):
    try:
        files, backup = chosen_disks(context)
    except LookupError as gone:
        return [str(gone)]
    chosen_ones = {disk["id"]: disk for disk in (files, backup) if disk}.values()
    found = [NOT_MOUNTED.format(disk_name(disk), point(disk)) for disk in chosen_ones
             if not (disk["mounted"] and disk["at_boot"])]
    if found:
        return found
    declared = f"{files['mounted']}/{FOLDER}" if files else None
    held = context.suite.files_folder()
    return [ELSEWHERE.format(held or OWN_VOLUME, declared or OWN_VOLUME)] if held != declared else None
