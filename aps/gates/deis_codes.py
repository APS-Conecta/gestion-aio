# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tree stays establishment-agnostic: no tracked file names an establishment's DEIS code.

The check matches the shape (a `deis` word followed by a 4-6 digit code), not a list of codes, so the
next establishment's code is caught too. Only the DEIS register data is exempt: it is the codes.
"""

import argparse
import subprocess
import sys

SHAPE = r"deis[a-z0-9_]*(\.py)?[\"'=: -]{1,4}[0-9]{4,6}([^0-9]|$)"
REGISTER = ":(exclude)aps/deis/*.csv"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    repo = parser.parse_args().repo
    r = subprocess.run(["git", "-C", repo, "grep", "-n", "-i", "-I", "-E", SHAPE, "--", ".", REGISTER],
                       capture_output=True, text=True)
    if r.returncode > 1:
        print(r.stderr, end="")
        return 2
    if r.stdout:
        print("deis-codes: tracked files naming a DEIS code:\n" + r.stdout, end="")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
