# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The components: AIO's optional containers the suite runs, on through AIO's own switches; every other one is off."""

NAME = "Componentes"
NEEDS = ()
# By AIO's switch, with the name «Revisar» and the progress show.
COMPONENTS = {"talk": "Talk", "eurooffice": "Euro-Office", "imaginary": "Imaginary", "clamav": "ClamAV (antivirus)",
              "fulltextsearch": "Búsqueda de texto completo", "harp": "HaRP (servidor de aplicaciones externas)"}


def named(container):
    """A container's name as the operator reads it: a component's from «Revisar», every other one AIO's."""
    return COMPONENTS.get(container["id"].removeprefix("nextcloud-aio-"), container["name"])


def run(context):
    context.suite.switch(COMPONENTS)
