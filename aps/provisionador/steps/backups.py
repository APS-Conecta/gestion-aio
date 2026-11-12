# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""AIO's own nightly backup, to the backup destination the operator chose on «Almacenamiento»: the backup folder on this
server, the aps-conecta/copias folder of the backup disk, which «Almacenamiento» mounted for good, or a repository on
another computer over SSH. The destination is tested first, so «Listo» never comes with a backup that cannot be made: a
file written in the folder and removed at once, or the other computer reached with AIO's key and Borg alone.
AIO's automatic updates are on too: AIO's nightly job (Containers/mastercontainer/daily-backup.sh) then keeps the
containers on the release's pins and updates the store's apps, which our host timer (install.sh) also does every
night."""

import re
from urllib.parse import urlsplit

from steps.storage import BACKUP_GONE, backup_disk, point

NAME = "Copias de seguridad"
NEEDS = ("storage",)
BACKUP_FOLDER = "/var/backups/aps-conecta"  # the backup folder: the backup destination on the system disk
BACKUP_TIME = "06:00"  # UTC, as AIO's clock runs: 02:00 or 03:00 in continental Chile
COPIES = "aps-conecta/copias"  # the backup's folder on a backup disk, beside the staff's files folder
WRITTEN = "Copia de seguridad: se pudo escribir en {}."
REACHED = "Copia de seguridad: {} responde por SSH con Borg."
NOT_A_COMPUTER = ("Indique el otro computador de la copia de seguridad: su nombre o dirección, su puerto SSH (de 1 a "
                  "65535), el usuario y la carpeta del repositorio, sin espacios ni «..».")
HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")  # a host's name or IPv4 address
USER = re.compile(r"[A-Za-z0-9._-]+")
REPOSITORY = re.compile(r"[A-Za-z0-9._/~-]+")  # /path, ./repo as hosting services name it, or ~/path
DIFFERS = ("AIO guarda la copia de seguridad en {} a las {} UTC, no en {} a las {} UTC. Presione «Reintentar»; si vuelve "
           "a ocurrir, contacte a APS Conecta.")


def remote(ssh):
    """The remote repository, ssh://user@host:port/folder, the other computer's fields make, as Borg names it; raises
    ValueError, NOT_A_COMPUTER, for fields that make none."""
    fields = ssh if isinstance(ssh, dict) else {}
    host, port, user, folder = (fields.get(key) for key in ("host", "port", "user", "folder"))
    if not (isinstance(host, str) and HOST.fullmatch(host) and type(port) is int and 0 < port < 65536
            and isinstance(user, str) and USER.fullmatch(user) and isinstance(folder, str) and REPOSITORY.fullmatch(folder)
            and ".." not in folder.split("/")):
        raise ValueError(NOT_A_COMPUTER)
    folder = folder if folder.startswith(("/", "./", "~")) else f"./{folder}"  # a bare folder is in the user's home
    return f"ssh://{user}@{host}:{port}{folder if folder.startswith('/') else '/' + folder}"


def destination(context):
    """Where AIO's nightly backup goes: a folder on this server or a remote repository. At an upgrade, the one AIO holds
    now, for after «Listo» AIO's page owns it; otherwise, and when AIO holds none, the one chosen on «Almacenamiento».
    Raises LookupError when the backup disk is gone."""
    held = context.suite.backup_settings()[0] if context.upgrade else None
    if held:
        return held
    ssh = (context.backups or {}).get("ssh")
    if ssh:
        return remote(ssh)
    disk = backup_disk(context)
    return f"{point(disk)}/{COPIES}" if disk else BACKUP_FOLDER


def run(context):
    try:
        target = destination(context)
    except LookupError:
        return [BACKUP_GONE]
    kept = False
    if target.startswith("ssh://"):
        context.suite.ssh_key()  # made when AIO has none yet, so the test and AIO's backup use it
        kept = context.suite.reachable(target)
        context.log(REACHED.format(urlsplit(target).hostname))
    else:
        context.suite.writable(target)
        context.log(WRITTEN.format(target))
    context.suite.back_up(target, BACKUP_TIME, kept)
    return None


def check(context):
    try:
        target = destination(context)
    except LookupError:
        return [BACKUP_GONE]
    held = context.suite.backup_settings()
    return [DIFFERS.format(*held, target, BACKUP_TIME)] if held != (target, BACKUP_TIME) else None
