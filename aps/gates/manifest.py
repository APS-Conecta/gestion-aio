# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Suite manifest, aps/provisionador/apps.json, is the only list of the apps the suite adds, and of the forks that
replace a component's app; the other components' apps come with AIO's switches. Each app has its Nextcloud id, its
Spanish name and its source: own or fork, from the App releases of its APS-Conecta repo, or store, from Nextcloud's
app store. An optional app, which «Revisar» offers off, is marked so and has the summary «Revisar» shows. No app names
a version: an own or fork app comes from its newest compatible App release, a store app from the store.
"""

import argparse
import json
import re
import sys
from pathlib import Path

MANIFEST = "aps/provisionador/apps.json"
FIELDS = ("id", "name", "source", "repo", "optional", "summary")


def text(value):
    return isinstance(value, str) and value.strip() != ""


def problems(apps):
    """What is wrong with the manifest, one line each."""
    if not (isinstance(apps, list) and apps):
        return ["it is not a list of apps"]
    found, ids, repos = [], [], []
    for n, app in enumerate(apps, 1):
        if not isinstance(app, dict):
            found.append(f"app {n}: not an object")
            continue
        where = f"app {n} ({app.get('id')})"
        unknown = [field for field in app if field not in FIELDS]
        if unknown:
            found.append(f"{where}: unknown fields {unknown}; no app names a version")
        if not (isinstance(app.get("id"), str) and re.fullmatch(r"[a-z][a-z0-9_]*", app["id"])):
            found.append(f"{where}: its id is not a Nextcloud app id")
        elif app["id"] in ids:
            found.append(f"{where}: its id is listed twice")
        ids.append(app.get("id"))
        if not text(app.get("name")):
            found.append(f"{where}: it has no Spanish name")
        source, repo = app.get("source"), app.get("repo")
        if source in ("own", "fork"):
            if not (isinstance(repo, str) and re.fullmatch(r"APS-Conecta/[A-Za-z0-9._-]+", repo)):
                found.append(f"{where}: an {source} app needs its APS-Conecta repo")
            elif repo in repos:
                found.append(f"{where}: its repo is listed twice")
            repos.append(repo)
        elif source == "store":
            if "repo" in app:
                found.append(f"{where}: a store app has no repo")
        else:
            found.append(f"{where}: its source is not own, fork or store")
        if app.get("optional", True) is not True:
            found.append(f"{where}: optional is true or left out")
        if "optional" in app and not text(app.get("summary")):
            found.append(f"{where}: an optional app needs the summary «Revisar» shows")
        if "optional" not in app and "summary" in app:
            found.append(f"{where}: only an optional app has a summary")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    path = Path(parser.parse_args().repo, MANIFEST)
    try:
        found = problems(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        found = [f"it does not read as JSON: {error}"]
    if found:
        print(f"manifest: {MANIFEST}:\n" + "\n".join(found))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
