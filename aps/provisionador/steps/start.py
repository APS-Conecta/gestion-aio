# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""AIO's start: it pulls, creates and starts every container, Apache last, then each container answers in turn.

The step goes by AIO's own reading of the containers. Once Apache runs, AIO started the suite (and closed its login), so
the step does not press start again. After a Provisionador restart cut the step short, AIO goes on with the start it was
sent; while it does, the step waits for it instead of sending another one.
"""

from steps import until
from steps.components import named
from suite import NOT_STARTED

NAME = "Inicio de la suite"
NEEDS = ("components", "backups")
APACHE = "nextcloud-aio-apache"
WAIT = 15 * 60  # seconds: as long as AIO's start may take
NOT_RUNNING = ("Estos contenedores de la suite no quedaron en ejecución: {}. Presione «Reintentar»; si vuelve a "
               "ocurrir, contacte a APS Conecta.")


def states(suite):
    return {c["id"]: c for c in suite.containers()}


def started(listed):
    return listed.get(APACHE, {}).get("state") == "running"


def problems(listed):
    late = [named(c) for c in listed.values() if c["state"] != "running"]
    return [NOT_RUNNING.format(", ".join(late))] if late else []


def run(context):
    listed = states(context.suite)
    if "start" in context.interrupted and any(c["state"] != "stopped" for c in listed.values()):
        if not started(until(lambda: states(context.suite), started, WAIT)):
            return [NOT_STARTED]
    elif not started(listed):
        context.suite.start()
    return problems(until(lambda: states(context.suite), lambda now: not problems(now), WAIT))


def check(context):
    return problems(states(context.suite))
