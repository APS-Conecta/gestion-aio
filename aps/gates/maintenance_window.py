# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Maintenance-window gate: the jobs install step and the thin Nextcloud image name the same hour.

Upstream's entrypoint sets Nextcloud's maintenance window at every start from the image's NEXTCLOUD_MAINTENANCE_WINDOW,
and the jobs step declares it as MAINTENANCE_WINDOW. Each file names one hour, the same one.
"""

import argparse
import re
import sys
from pathlib import Path

HOURS = {"aps/provisionador/steps/jobs.py": re.compile(r"(?m)^MAINTENANCE_WINDOW = (\d+)\b"),
         "aps/images/nextcloud/Dockerfile": re.compile(r"(?m)^ENV NEXTCLOUD_MAINTENANCE_WINDOW=(\d+)$")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    repo = Path(parser.parse_args().repo)
    named, wrong = {}, []
    for path, hour in HOURS.items():
        try:
            found = hour.findall((repo / path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            found = []
        if len(found) == 1:
            named[path] = int(found[0])
        else:
            wrong.append(f"{path} names no maintenance-window hour, or more than one")
    if not wrong and len(set(named.values())) > 1:
        wrong.append("the hours differ: " + ", ".join(f"{path}: {hour}" for path, hour in named.items()))
    if wrong:
        print("maintenance window:\n" + "\n".join(wrong))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
