# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""«IA local», the suite's AI: an optional step, off by default, that the operator turns on or off on «Revisar»
whenever the wizard is open, once «Instalar» was pressed too.

On, our «ollama» community container (community-containers/ollama) runs beside Nextcloud through AIO's
community-container switch, as Caddy does, with the GPU the suite port found: an NVIDIA one through the NVIDIA Container
Toolkit, an AMD or Intel one through the DRI devices, otherwise the CPU. Ollama answers one request at a time (its
definition says so) under a memory cap: what the server's memory leaves beside the suite's own floor. The model, the
release's pin in model.json, is pulled by its tag and kept only at its pinned digest, and Nextcloud's OpenAI-compatible
connector (integration_openai, a store app the step enables, as a component's app comes with its switch) points at
Ollama with that model and its chat endpoint on: the suite then has a text-generation provider, and Estadística's nota
appears.

Off, the connector is disabled, so there is no provider, and the container stops and leaves AIO's list; its volume, with
the model, stays. A Suite release pins Ollama's image (its definition's image_tag) and the model by digest, and only a
release moves them: AIO's nightly job restarts the container on the same image, and nothing pulls the model again once
Ollama holds it at its digest.
"""

import json
from pathlib import Path

from steps import apps, configure, differences, until
from suite import OLLAMA, Unreachable

NAME = "IA local"
NEEDS = ("start",)
MODEL = json.loads((Path(__file__).resolve().parents[1] / "model.json").read_text(encoding="utf-8"))  # tag, digest
APP = "integration_openai"  # Nextcloud's OpenAI-compatible connector, a store app
SETTINGS = {"apps": {APP: {"url": "http://nextcloud-aio-ollama:11434/v1", "default_completion_model_id": MODEL["model"],
                           "chat_endpoint_enabled": "1"}}}
# The suite's own memory in GiB beside the AI: the bootstrap's floor (aps/bootstrap/install.sh, MIN_MEMORY_GIB; the
# «IA local» tests hold this to it), the suite's peak with every component on.
SUITE_MEMORY_GIB = 6
WAIT = 2 * 60  # seconds Ollama may take to answer once its container started
# What «Revisar» says the server offers the model, by the GPU the suite port found.
FOUND = {"nvidia": "El servidor tiene una GPU NVIDIA con su NVIDIA Container Toolkit: la IA local la usará.",
         "dri": "El servidor tiene una GPU AMD o Intel: la IA local la usará.",
         None: "El servidor no tiene ninguna GPU que la IA local pueda usar: usará el procesador y responderá más "
               "lento."}

NO_MEMORY = ("El servidor tiene {} GiB de memoria y APS Conecta Gestión usa {} GiB: no queda memoria para la IA local. "
             "Desactívela en «Revisar», o amplíe la memoria del servidor y presione «Reintentar».")
SILENT = "La IA local no responde. Presione «Reintentar» en unos minutos; si vuelve a ocurrir, contacte a APS Conecta."
PULLING = "«IA local»: se descarga el modelo {}; puede tardar varios minutos."
MOVED = ("El modelo {} que publica Ollama ya no es el que fija esta versión de APS Conecta Gestión, y la IA local no "
         "lo usa. Contacte a APS Conecta.")
STOPPED = ("El contenedor de la IA local no está en ejecución. Presione «Reintentar»; si vuelve a ocurrir, contacte a "
           "APS Conecta.")
MISSING = ("Ollama ya no tiene el modelo {} en la versión que fija APS Conecta Gestión. Presione «Reintentar»; si "
           "vuelve a ocurrir, contacte a APS Conecta.")
STILL_ON = ("La IA local sigue activa aunque se desactivó en «Revisar». Presione «Reintentar»; si vuelve a ocurrir, "
            "contacte a APS Conecta.")
DISABLED = ("El conector de la IA local no está activado en Nextcloud. Presione «Reintentar»; si vuelve a ocurrir, "
            "contacte a APS Conecta.")


def held(suite):
    """The models Ollama holds, each tag's digest; None while it does not answer."""
    try:
        return suite.models()
    except Unreachable:
        return None


def pinned(models):
    return (models or {}).get(MODEL["model"]) == MODEL["digest"]


def ollama(suite):
    """Ollama's container as AIO lists it, or None when AIO does not run it."""
    return next((c for c in suite.containers() if c["id"] == OLLAMA), None)


def on(suite):
    """Whether any part of the AI is on: the connector or Ollama's container."""
    return APP in apps.enabled(suite) or ollama(suite) is not None


def run(context):
    suite = context.suite
    if not context.ai:
        if APP in apps.enabled(suite):
            suite.occ("app:disable", APP)
        if ollama(suite) is not None:
            suite.switch_ollama(None)
        return []
    memory = suite.hardware()["memory_gib"]
    if memory <= SUITE_MEMORY_GIB:
        return [NO_MEMORY.format(memory, SUITE_MEMORY_GIB)]
    suite.switch_ollama({"gpu": suite.gpu(), "memory_gib": memory - SUITE_MEMORY_GIB})
    models = until(lambda: held(suite), lambda found: found is not None, WAIT)
    if models is None:
        return [SILENT]
    if not pinned(models):
        context.log(PULLING.format(MODEL["model"]))
        # Ollama pulls by tag alone (it refuses a digest): once it re-tags the model, a fresh install pulls another
        # digest and stops here, MOVED, until a release moves the pin.
        suite.pull(MODEL["model"])
        if not pinned(suite.models()):
            return [MOVED.format(MODEL["model"])]
    suite.occ("app:enable", APP)
    return configure(suite, SETTINGS)


def check(context):
    suite = context.suite
    if not context.ai:
        return [STILL_ON] if on(suite) else []
    if (ollama(suite) or {}).get("state") != "running":
        return [STOPPED]
    if not pinned(held(suite)):
        return [MISSING.format(MODEL["model"])]
    if APP not in apps.enabled(suite):
        return [DISABLED]
    return differences(suite, SETTINGS)
