#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The staff's files come back after a reboot: a staff member stores a file in their own files, which lands in AIO's
data folder (on the second disk when «Almacenamiento» moved it there), the machine reboots, and once it answers again
that folder is mounted from the same disk as before, the suite answers at its name and the file comes back, the same
bytes. It runs last: the reboot ends hardware.sh's sampler, whose peaks cover every stage before it."""

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
from browser import as_person, files_folder, machine, person, suite_name  # noqa: E402

USER, FILE = "prueba.reinicio", "reinicio.txt"
BODY = "Guardado antes de reiniciar el servidor.\n".encode()
BACK = 15 * 60  # seconds the machine, then the suite, may take to answer again
BOOT_ID = "/proc/sys/kernel/random/boot_id"  # new at every boot


def data_folder():
    """AIO's data folder, the staff's files folder, with the disk and the mount point that hold it."""
    folder = files_folder() or machine("sudo docker volume inspect --format '{{.Mountpoint}}' "
                                       "nextcloud_aio_nextcloud_data").strip()
    return folder, machine(f"sudo findmnt --noheadings --output SOURCE,TARGET --target {folder}").split()


def stored():
    """Whether the suite hands the staff member back the file they stored; False while it does not answer at all."""
    try:
        return as_person(USER, password, "GET", url) == (200, BODY)
    except OSError:
        return False


def rebooted():
    """Whether the machine runs a new boot, not the one before the reboot, and its Docker answers."""
    answer = subprocess.run([os.environ["MACHINE"], f"cat {BOOT_ID} && sudo docker info > /dev/null"],
                            capture_output=True, text=True, check=False)
    return answer.returncode == 0 and answer.stdout.strip() != boot


def until(answers, what):
    deadline = time.monotonic() + BACK
    while not answers():
        if time.monotonic() > deadline:
            sys.exit(f"{what} did not come back within {BACK // 60} minutes of the reboot")
        time.sleep(10)


suite = f"https://{suite_name()}"
url = f"{suite}/remote.php/dav/files/{USER}/{FILE}"
password = person(USER, "Prueba Reinicio")
status, _ = as_person(USER, password, "PUT", url, BODY)
if status not in (201, 204):
    sys.exit(f"{USER} could not store {FILE} in their own files: HTTP {status}")
folder, held = data_folder()
boot = machine(f"cat {BOOT_ID}").strip()
machine(f"sudo test -f {folder}/{USER}/files/{FILE}")
print(f"{FILE} lies in AIO's data folder {folder}, on {held[0]} mounted at {held[1]}; rebooting")

subprocess.run([os.environ["MACHINE"], "sudo systemctl reboot"], capture_output=True, check=False)
until(rebooted, "The machine, rebooted, with Docker,")
now = data_folder()
if now != (folder, held):
    sys.exit(f"after the reboot AIO's data folder is {now[0]} on {now[1]}, not {folder} on {held}")
until(stored, f"{FILE}, from the suite at {suite},")
print(f"after the reboot {held[0]} is mounted at {held[1]} again, the suite answers at {suite} and {FILE} is there")
