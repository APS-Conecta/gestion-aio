# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""«Almacenamiento»: what each disk of the server may hold, the staff's files or the nightly backup, or why it can't.
The staff's files go to the disk with the most free space unless the operator chooses otherwise; the backup goes where
the operator chooses, and is chosen already only where the backup folder is the one place it may go. The suite port
reads the disks; this module only decides.

The system disk, the one holding Docker's storage, may hold both, as it does on every server today: the files in AIO's
own volume, the backup in the backup folder. Any other disk must carry a filesystem whose owners and permissions AIO's
containers keep, and a USB disk holds backups only, so that an unplugged cable never stops the suite. Each disk holds a
role only with room for it, the bootstrap's floors: another disk the install's own files' or a first backup's, the
system disk the program's besides them, and besides the backup's too when it holds the files and the backup has nowhere
else to go."""

import re
from pathlib import Path

from steps.backups import BACKUP_FOLDER

# The floors' one source, the bootstrap, which the bundle carries beside the Provisionador for this.
BOOTSTRAP = Path(__file__).resolve().parent.parent / "bootstrap" / "install.sh"

LINUX = {"ext4", "xfs", "btrfs"}
WINDOWS = {"ntfs", "exfat", "vfat"}
UNFORMATTED = "sin formato"
WINDOWS_FORMAT = "formato de Windows"
OTHER_FORMAT = "formato {}, no compatible"
FREE_UNREAD = "no se pudo leer su espacio libre"
USB_BACKUPS_ONLY = "disco USB: solo para copias"
NO_ROOM = "sin espacio suficiente"


def floors():
    """The room in bytes the bootstrap requires: the program's on the system disk, the install's own files' and a
    first backup's."""
    script = BOOTSTRAP.read_text(encoding="utf-8")
    return {role: int(re.search(rf"^{variable}=(\d+)$", script, re.M)[1]) * 10 ** 9
            for role, variable in (("program", "MIN_PROGRAM_GB"), ("files", "MIN_FILES_GB"),
                                   ("backup", "MIN_BACKUP_GB"))}


FLOORS = floors()


def roles(disk):
    """(may hold the staff's files, may hold the backup, why not in Spanish or None)."""
    if disk["system"]:
        return True, True, None
    filesystem = disk["filesystem"]
    if not filesystem:
        return False, False, UNFORMATTED
    if filesystem in WINDOWS:
        return False, False, WINDOWS_FORMAT
    if filesystem not in LINUX:
        return False, False, OTHER_FORMAT.format(filesystem)
    if disk["free"] is None:
        return False, False, FREE_UNREAD
    if disk["usb"]:
        return False, True, USB_BACKUPS_ONLY
    return True, True, None


def limit(disk, role, need):
    """The disk keeps the role only with `need` bytes free; without them it no longer holds it, for lack of room."""
    if disk[role] and disk["free"] < need:
        disk[role], disk["reason"] = False, NO_ROOM


def most_free(disks):
    """The disk with the most free space; on a tie the first, the system disk coming first."""
    return max(disks, key=lambda disk: disk["free"], default=None)


def shown(disks, files=None, installed=False):
    """«Almacenamiento» as GET /api/storage answers it: each disk with its roles, then the files disk and the backup
    destination. The files disk is the one the operator chose, by its id; with none chosen, the system disk once
    «Instalar» was pressed, which keeps a server installed before «Almacenamiento» where its files are, and before it,
    the disk with the most free space that may hold them. The backup destination is the backup folder when no other
    disk may hold the backup, otherwise None, for the operator to choose."""
    listed = [{**disk, **dict(zip(("files", "backups", "reason"), roles(disk)))} for disk in disks]
    system = next(disk for disk in listed if disk["system"])
    for disk in listed:
        if disk is not system:
            limit(disk, "files", FLOORS["files"])
            limit(disk, "backups", FLOORS["backup"])
    alone = not any(disk["backups"] for disk in listed if disk is not system)
    limit(system, "files", FLOORS["program"] + FLOORS["files"] + (FLOORS["backup"] if alone else 0))
    if not files:
        found = system if installed else most_free([disk for disk in listed if disk["files"]])
        files = found and found["id"]
    on_system = files == system["id"]
    limit(system, "backups", FLOORS["program"] + FLOORS["backup"] + (FLOORS["files"] if on_system else 0))
    backups = {"folder": True} if alone and system["backups"] else None
    return {"disks": listed, "files": files, "files_locked": installed, "backups": backups, "folder": BACKUP_FOLDER,
            "same_disk": bool(backups) and on_system}
