# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release workflow publishes only what passed. It creates every release as a pre-release kept from «Latest», and
a job makes a release «Latest» only once every check of that candidate passed: the clean-machine acceptance run, the
one command on a fresh runner and the host checks. So a red run leaves nothing that the one command
(releases/latest/download/install.sh) installs.

It reads the workflow's jobs as written: each job's `needs`, its `if` and its lines.
"""

import argparse
import re
import sys
from pathlib import Path

WORKFLOW = ".github/workflows/aps-release.yml"
CHECKS = ("acceptance", "bootstrap", "host-checks")  # the jobs that check the candidate
STATUS = re.compile(r"\b(always|success|failure|cancelled)\s*\(")  # any of them drops the implicit success()
PUBLISHES = {"--latest", "--latest=true", "--prerelease=false"}  # `gh release edit` flags that can make it «Latest»


def jobs(text):
    """Each job by its id: the jobs it needs, its `if` ("" when it has none) and its lines."""
    found = {}
    for block in re.split(r"(?m)^(?=  [\w-]+:$)", text.split("\njobs:\n", 1)[-1]):
        head = re.match(r"  ([\w-]+):\n", block)
        if head:
            needs = re.search(r"(?m)^    needs: *(.+)$", block)
            condition = re.search(r"(?m)^    if: *(.+)$", block)
            found[head[1]] = {"needs": re.findall(r"[\w-]+", needs[1]) if needs else [],
                              "if": condition[1] if condition else "", "text": block}
    return found


def commands(text, verb):
    """Each `gh release <verb>` command line, with its continuation lines, as its words."""
    return [c.split() for c in re.findall(rf"gh release {verb}\b(?:[^\n]*\\\n)*[^\n]*", text)]


def problems(text):
    """What breaks the rules, one line each."""
    found = [f"{' '.join(c[:4])}: a release is not created as a pre-release kept from «Latest»"
             for c in commands(text, "create") if not {"--prerelease", "--latest=false"} <= set(c)]
    every = jobs(text)
    publishing = [name for name, job in every.items()
                  if any(PUBLISHES & set(c) for c in commands(job["text"], "edit"))]
    if not publishing:
        found.append("no job makes a release «Latest»")
    for name in publishing:
        missing = [check for check in CHECKS if check not in every[name]["needs"]]
        if missing:
            found.append(f"{name} makes a release «Latest» without waiting for {', '.join(missing)}")
        if STATUS.search(every[name]["if"]):
            found.append(f"{name} makes a release «Latest» even when a check failed: if: {every[name]['if']}")
    for check in CHECKS:
        if check not in every:
            found.append(f"no job {check} checks the candidate")
        elif "continue-on-error:" in every[check]["text"]:
            found.append(f"{check} passes even when it fails: it continues on error")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", default=WORKFLOW)
    path = parser.parse_args().workflow
    try:
        found = problems(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        found = [f"it cannot be read: {error}"]
    if found:
        print(f"release: {path}:\n" + "\n".join(found))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
