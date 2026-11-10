# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins gate: every image the suite runs, but our own, and the model «IA local» pulls are pinned by digest.

An image_tag in php/containers.json and in each community container the keep-list declares (the ones the suite switches
on) is a sha256 digest, but for our own images, which each release builds and pins in its bundle's images.lock. The
model, aps/provisionador/model.json, names its tag and its digest, never a bare tag. So only a Suite release moves
them: AIO's nightly job pulls the same digests, and Ollama keeps the model it holds.
"""

import argparse
import json
import re
import sys
from pathlib import Path

KEEP_LIST = "aps/keep-list.txt"
MODEL = "aps/provisionador/model.json"
OURS = "ghcr.io/aps-conecta/"  # our images: each release builds them and pins them in images.lock
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TAG = re.compile(r"[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)?:[a-z0-9._-]+")


def definitions(repo):
    """php/containers.json and the community containers' definitions the keep-list declares."""
    listed = [line.split()[0] for line in (repo / KEEP_LIST).read_text(encoding="utf-8").splitlines()
              if line.startswith("community-containers/")]
    found = [repo / "php/containers.json"]
    for path in listed:
        found += sorted((repo / path).glob("*.json")) if path.endswith("/") else [repo / path]
    return found


def problems(repo):
    found = []
    for path in definitions(repo):
        for service in json.loads(path.read_text(encoding="utf-8"))["aio_services_v1"]:
            image, tag = service["image"], service["image_tag"]
            if not (image.startswith(OURS) or DIGEST.fullmatch(tag)):
                found.append(f"{path.relative_to(repo)}: {service['container_name']} runs {image}:{tag}, not pinned "
                             "by digest")
    model = json.loads((repo / MODEL).read_text(encoding="utf-8"))
    if not (TAG.fullmatch(str(model.get("model"))) and DIGEST.fullmatch(str(model.get("digest")))):
        found.append(f"{MODEL}: {model.get('model')} is not pinned by its tag and digest")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    repo = Path(parser.parse_args().repo)
    try:
        found = problems(repo)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        found = [f"the pins do not read: {error!r}"]
    if found:
        print("pins:\n" + "\n".join(found))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
