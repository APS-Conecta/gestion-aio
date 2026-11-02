# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vanilla conformance gate: the tree may differ from upstream only where the keep-list says so.

The keep-list's header names the upstream base commit (`# base: <sha> ...`). Every other line is
`<path> <reason>`; a path ending in `/` covers everything under it.
"""

import argparse
import subprocess
import sys
from pathlib import Path

KEEP_LIST = "aps/keep-list.txt"


def changed_paths(repo, base):
    out = subprocess.run(["git", "-C", repo, "diff", "--name-status", "--no-renames", base, "HEAD"],
                         check=True, capture_output=True, text=True).stdout
    return [tuple(line.split("\t", 1)) for line in out.splitlines()]


def read_keep_list(repo):
    """Return (base, entries, problems)."""
    base, entries, problems = None, [], []
    for n, line in enumerate((Path(repo) / KEEP_LIST).read_text().splitlines(), 1):
        if line.startswith("# base:") and len(line.split()) > 2:
            base = line.split()[2]
        elif line.strip() and not line.startswith("#"):
            fields = line.split(None, 1)
            entries.append(fields[0])
            if len(fields) < 2:
                problems.append(f"line {n}: {fields[0]} has no reason")
    if not base:
        problems.append("no '# base: <upstream commit>' header")
    return base, entries, problems


def covers(entry, path):
    return path == entry or (entry.endswith("/") and path.startswith(entry))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    repo = parser.parse_args().repo
    base, entries, problems = read_keep_list(repo)
    if not base:
        print(f"conformance: {KEEP_LIST}: " + "; ".join(problems))
        return 1
    problems = [f"{KEEP_LIST}: {p}" for p in problems]
    changed = changed_paths(repo, base)
    undeclared = [f"{status} {path}" for status, path in changed if not any(covers(e, path) for e in entries)]
    stale = [e for e in entries if not any(covers(e, path) for _, path in changed)]
    if undeclared:
        problems.append("differs from upstream outside the keep-list:\n  " + "\n  ".join(undeclared))
    if stale:
        problems.append("stale keep-list entries (no difference from upstream):\n  " + "\n  ".join(stale))
    for p in problems:
        print("conformance: " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
