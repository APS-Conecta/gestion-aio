# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The components: AIO's optional containers the suite runs, on through AIO's own switches; every other one is off.
Talk recording is one of them on a server that passes the recording threshold, with no choice for the operator."""

NAME = "Componentes"
NEEDS = ()
# By AIO's switch, with the name «Revisar» and the progress show.
COMPONENTS = {"talk": "Talk", "eurooffice": "Euro-Office", "imaginary": "Imaginary", "clamav": "ClamAV (antivirus)",
              "fulltextsearch": "Búsqueda de texto completo", "harp": "HaRP (servidor de aplicaciones externas)"}
RECORDING = "talk-recording"  # AIO's switch for Talk's recording server
# The least a server needs to record Talk calls: the suite's minimum (aps/bootstrap/install.sh: 2 cores, 6 GiB) plus one
# recording, which takes about 200% CPU and a 2 GiB browser (nextcloud/nextcloud-talk-recording, docs/installation.md,
# «Hardware requirements»). Memory in GiB, rounded as the bootstrap rounds it.
RECORDING_THRESHOLD = {"cpus": 4, "memory_gib": 8}


def chosen(hardware):
    """AIO's switches to turn on for a server with this hardware: the components, and recording if it passes the
    threshold."""
    records = all(hardware[key] >= least for key, least in RECORDING_THRESHOLD.items())
    return [*COMPONENTS, *([RECORDING] if records else [])]


def named(container):
    """A container's name as the operator reads it: a component's from «Revisar», every other one AIO's."""
    return COMPONENTS.get(container["id"].removeprefix("nextcloud-aio-"), container["name"])


def run(context):
    context.suite.switch(chosen(context.suite.hardware()))
