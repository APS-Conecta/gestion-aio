# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""«Almacenamiento»: the staff's files on the files disk the operator chose. Before AIO first creates Nextcloud's
container, the disk is mounted for good under /mnt/aps-conecta/<uuid> (a disk already mounted at boot keeps its mount
point) and AIO keeps the staff's files in its aps-conecta/archivos folder: its NEXTCLOUD_DATADIR, which the bootstrap's
mastercontainer start passes from then on. Files on the system disk, and an install that stored no files disk, stay in
AIO's own volume. Nothing is formatted, partitioned or deleted."""

from suite import MOUNTS

NAME = "Almacenamiento"
NEEDS = ()
FOLDER = "aps-conecta/archivos"  # the staff's files folder on the files disk
GONE = ("El disco elegido para los archivos del personal ya no está en el servidor. Conéctelo y presione "
        "«Reintentar».")
MOUNTED = "Disco {} montado en {}."
NOT_MOUNTED = ("El disco de los archivos del personal ya no queda montado en {} en cada arranque del servidor. "
               "Presione «Reintentar»; si vuelve a ocurrir, contacte a APS Conecta.")
ELSEWHERE = ("AIO guarda los archivos del personal en {}, no en {}. Presione «Reintentar»; si vuelve a ocurrir, "
             "contacte a APS Conecta.")
OWN_VOLUME = "su propio volumen"


def files_disk(context):
    """The files disk stored on «Almacenamiento», as the suite port reads it now; None when the staff's files stay in
    AIO's own volume. Raises LookupError when that disk is gone."""
    if context.files is None:
        return None
    disk = next((found for found in context.suite.disks() if found["id"] == context.files), None)
    if disk is None:
        raise LookupError(context.files)
    return None if disk["system"] else disk


def disk_name(disk):
    return f"{disk['model'] or 'Disco'} ({disk['device']})"


def run(context):
    try:
        disk = files_disk(context)
    except LookupError:
        return [GONE]
    if disk is None:
        return None
    point = disk["mounted"]
    if not (point and disk["at_boot"]):
        point = context.suite.mount(disk)
        context.log(MOUNTED.format(disk_name(disk), point))
    context.suite.place_files(f"{point}/{FOLDER}")
    return None


def check(context):
    try:
        disk = files_disk(context)
    except LookupError:
        return [GONE]
    if disk and not (disk["mounted"] and disk["at_boot"]):
        return [NOT_MOUNTED.format(disk["mounted"] or f"{MOUNTS}/{disk['id']}")]
    declared = f"{disk['mounted']}/{FOLDER}" if disk else None
    held = context.suite.files_folder()
    return [ELSEWHERE.format(held or OWN_VOLUME, declared or OWN_VOLUME)] if held != declared else None
