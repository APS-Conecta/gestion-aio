# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""«Almacenamiento»: what each disk of the server may hold, the staff's files or the nightly backup, or why it can't.
The staff's files go to the disk with the most free space unless the operator chooses otherwise; the backup goes where
the operator chooses, and is chosen already only where the backup folder is the one place it may go. The suite port
reads the disks; this module only decides.

The system disk, the one holding Docker's storage, always may hold both, as it does on every server today: the files in
AIO's own volume, the backup in the backup folder. Any other disk must carry a filesystem whose owners and permissions
AIO's containers keep, and a USB disk holds backups only, so that an unplugged cable never stops the suite."""

from steps.backups import BACKUP_FOLDER

LINUX = {"ext4", "xfs", "btrfs"}
WINDOWS = {"ntfs", "exfat", "vfat"}
UNFORMATTED = "sin formato"
WINDOWS_FORMAT = "formato de Windows"
OTHER_FORMAT = "formato {}, no compatible"
FREE_UNREAD = "no se pudo leer su espacio libre"
USB_BACKUPS_ONLY = "disco USB: solo para copias"


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


def most_free(disks):
    """The disk with the most free space; on a tie the first, the system disk coming first."""
    return max(disks, key=lambda disk: disk["free"], default=None)


def shown(disks):
    """«Almacenamiento» as GET /api/storage answers it: each disk with its roles, then the files disk, the one with the
    most free space that may hold them, and the backup destination: the backup folder when no other disk may hold the
    backup, otherwise None, for the operator to choose."""
    listed = [{**disk, **dict(zip(("files", "backups", "reason"), roles(disk)))} for disk in disks]
    files = most_free([disk for disk in listed if disk["files"]])
    alone = not any(disk["backups"] for disk in listed if not disk["system"])
    return {"disks": listed, "files": files["id"], "backups": {"folder": True} if alone else None,
            "folder": BACKUP_FOLDER, "same_disk": alone and files["system"]}
