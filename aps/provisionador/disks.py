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
    """Why the disk may not hold the staff's files and the backup, {"files": reason, "backups": reason}, in Spanish,
    None where it may."""
    if disk["system"]:
        return {"files": None, "backups": None}
    filesystem = disk["filesystem"]
    refused = (UNFORMATTED if not filesystem else WINDOWS_FORMAT if filesystem in WINDOWS
               else OTHER_FORMAT.format(filesystem) if filesystem not in LINUX
               else FREE_UNREAD if disk["free"] is None else None)
    if refused:
        return {"files": refused, "backups": refused}
    return {"files": USB_BACKUPS_ONLY if disk["usb"] else None, "backups": None}


def limit(disk, role, need):
    """The disk keeps the role only with `need` bytes free; without them it no longer holds it, for lack of room."""
    if disk["reasons"][role] is None and disk["free"] < need:
        disk["reasons"][role] = NO_ROOM


def most_free(disks):
    """The disk with the most free space; on a tie the first, the system disk coming first."""
    return max(disks, key=lambda disk: disk["free"], default=None)


def shown(disks, files=None, backups=None, installed=False):
    """«Almacenamiento» as GET /api/storage answers it: each disk with why it may not hold each part, then the files disk
    and the backup destination.

    Until «Instalar», each disk holds a part only with room for it, and for the other part when that sits there too;
    the system disk also keeps the suite's room. From «Instalar» on the suite already takes its room, and the floors no
    longer apply. The files disk is the one the operator chose, by its id; with none chosen, the system disk once
    «Instalar» was pressed, which keeps a server installed before «Almacenamiento» where its files are, and before it,
    the disk with the most free space that may hold them, or None when no disk has room for them. The backup
    destination, {"folder": True} for the backup folder on the system disk or {"disk": id}, is the one the operator
    chose; with none, the backup folder once «Instalar» was pressed, as on a server installed before «Almacenamiento»,
    or when no other disk may hold the backup, one drive being one disk, and the system disk may; otherwise None, for
    the operator to choose. The backup shares the files disk when it sits on that disk's
    drive."""
    listed = [{**disk, "reasons": roles(disk)} for disk in disks]
    system = next(disk for disk in listed if disk["system"])
    floors = dict.fromkeys(FLOORS, 0) if installed else FLOORS
    backup_disk = (backups or {}).get("disk")
    for disk in listed:
        if disk is not system:
            limit(disk, "files", floors["files"] + (floors["backup"] if backup_disk == disk["id"] else 0))
            limit(disk, "backups", floors["backup"] + (floors["files"] if files == disk["id"] else 0))
    alone = not any(disk["reasons"]["backups"] is None for disk in listed if disk["drive"] != system["drive"])
    in_folder = backups == {"folder": True} or (backups is None and alone)
    limit(system, "files", floors["program"] + floors["files"] + (floors["backup"] if in_folder else 0))
    if not files:
        found = system if installed else most_free([disk for disk in listed if disk["reasons"]["files"] is None])
        files = found and found["id"]
    limit(system, "backups", floors["program"] + floors["backup"] + (floors["files"] if files == system["id"] else 0))
    if backups is None and (installed or alone and system["reasons"]["backups"] is None):
        backups = {"folder": True}
    drives = {disk["id"]: disk["drive"] for disk in listed}
    held = system["drive"] if backups == {"folder": True} else drives.get(backup_disk)
    return {"disks": [{**disk, "files": disk["reasons"]["files"] is None, "backups": disk["reasons"]["backups"] is None}
                      for disk in listed],
            "files": files, "files_locked": installed, "backups": backups, "folder": BACKUP_FOLDER,
            "same_disk": bool(backups) and held is not None and held == drives.get(files)}
