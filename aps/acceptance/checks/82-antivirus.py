#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The antivirus as staff meet it: the EICAR test file a person uploads is refused and deleted, and one that reached
their files without the upload's scan is deleted by the antivirus's background scan, whose infected-file action is
delete, still after 60-theme restarted Nextcloud's container. AIO's cron runs that scan; the check runs it at once
instead of waiting."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser import NEXTCLOUD, as_person, machine, occ, person, suite_name  # noqa: E402

USER, UPLOADED, UNSCANNED = "prueba.antivirus", "eicar-subido.txt", "eicar-sin-revisar.txt"
EICAR = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR" + r"-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"  # halved: no repo file holds it

files = f"https://{suite_name()}/remote.php/dav/files/{USER}"
password = person(USER, "Prueba Antivirus")


def held(name):
    """Whether the person's files hold the file, as their own client lists them."""
    return as_person(USER, password, "PROPFIND", f"{files}/{name}", headers={"Depth": "0"})[0] == 207


status, answer = as_person(USER, password, "PUT", f"{files}/{UPLOADED}", EICAR.encode())
print(f"uploading the EICAR test file answered {status}: {answer.decode(errors='replace')[-300:]}")
if 200 <= status < 300 or held(UPLOADED):
    sys.exit("the antivirus let the uploaded EICAR test file into the person's files")

# Written into the person's folder on the server, then listed by Nextcloud's own scan: the upload's scan never saw it.
machine(f"{NEXTCLOUD} sh -c 'cat > \"$(php occ config:system:get datadirectory)/{USER}/files/{UNSCANNED}\"'", EICAR)
occ(f"files:scan --path=/{USER}/files")
if not held(UNSCANNED):
    sys.exit(f"{UNSCANNED}, written into the person's folder, is not in their files")
print(occ("files_antivirus:background-scan").strip())
if held(UNSCANNED):
    sys.exit("the antivirus's background scan did not delete the EICAR test file")
print("the uploaded EICAR test file was refused and deleted, and the background scan deleted the one it found")
