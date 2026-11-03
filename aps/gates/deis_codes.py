# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tree stays establishment-agnostic: no tracked file outside the DEIS register names one of its codes.

The codes come from the register itself (`codigo` in aps/deis/*.csv), so a code is caught with or without
a label and a date is never taken for one. A code inside a longer number or a hexadecimal string (a
checksum, a digest) is not a match. The register is the only exempt file: it is the codes.
"""

import argparse
import csv
import re
import subprocess
import sys

REGISTER = "aps/deis/*.csv"


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def register_codes(repo):
    codes = set()
    for path in git(repo, "ls-files", "--", REGISTER).stdout.split():
        with open(f"{repo}/{path}", encoding="utf-8", newline="") as fh:
            codes.update(row.get("codigo") or "" for row in csv.DictReader(fh))
    return codes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    repo = parser.parse_args().repo
    codes = register_codes(repo)
    if not codes or not all(re.fullmatch(r"[0-9]+", c) for c in codes):
        print(f"deis-codes: no DEIS register with numeric codes at {REGISTER}")
        return 2
    pattern = r"(?<![0-9a-f])(" + "|".join(sorted(codes)) + r")(?![0-9a-f])"
    r = git(repo, "grep", "-n", "-i", "-I", "-P", "-e", pattern, "--", ".", f":(exclude){REGISTER}")
    if r.returncode > 1:
        print(r.stderr, end="")
        return 2
    if r.stdout:
        print("deis-codes: tracked files naming a DEIS register code:\n" + r.stdout, end="")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
