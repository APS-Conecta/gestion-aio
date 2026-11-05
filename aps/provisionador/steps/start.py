# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""AIO's start: it pulls, creates and starts every container, Apache last, then each container answers in turn.

The step goes by AIO's own reading of the containers. Once Apache runs, AIO started the suite (and closed its login), so
the step does not press start again. After a Provisionador restart cut the step short, AIO goes on with the start it was
sent; while it does, the step waits for it instead of sending another one. While the step waits, AIO's start included,
the install's log gets AIO's first reading of each container and each change in it, and, while some container does not
run yet, how many do.
"""

import contextlib
import threading
import time

from steps import POLL, until
from steps.components import named
from suite import NOT_STARTED, Unreachable

NAME = "Inicio de la suite"
NEEDS = ("components", "backups")
APACHE = "nextcloud-aio-apache"
WAIT = 15 * 60  # seconds: as long as AIO's start may take
NOT_RUNNING = ("Estos contenedores de la suite no quedaron en ejecución: {}. Presione «Reintentar»; si vuelve a "
               "ocurrir, contacte a APS Conecta.")
CHANGED = "Contenedor «{}»: {}."
STATES = {"stopped": "en espera", "starting": "iniciando", "running": "en ejecución"}  # as the progress shows them
COUNTED = NAME + ": {} de {} contenedores en ejecución; AIO sigue descargando e iniciando el resto."
# Seconds: while some container does not run, its count goes to the log at least this often, and when it changes, but
# never twice within half of it.
HEARTBEAT = 2 * 60


def states(suite):
    return {c["id"]: c for c in suite.containers()}


def started(listed):
    return listed.get(APACHE, {}).get("state") == "running"


def problems(listed):
    late = [named(c) for c in listed.values() if c["state"] != "running"]
    return [NOT_RUNNING.format(", ".join(late))] if late else []


@contextlib.contextmanager
def watched(context, first):
    """While the block runs, AIO's readings of the containers, `first` the first and one last once the block ends, in
    the install's log: each container's state as first read and whenever it changes, and the count of those running,
    while some do not, as HEARTBEAT says."""
    seen, counted, at = {}, None, float("-inf")
    done = threading.Event()

    def read(listed):
        nonlocal seen, counted, at
        for id_, c in listed.items():
            if seen.get(id_, {}).get("state") != c["state"]:
                context.log(CHANGED.format(named(c), STATES[c["state"]]))
        seen = listed
        running, now = sum(c["state"] == "running" for c in listed.values()), time.monotonic()
        if running < len(listed) and (now - at >= HEARTBEAT or running != counted and now - at >= HEARTBEAT / 2):
            context.log(COUNTED.format(running, len(listed)))
            counted, at = running, now

    def watch():
        last = False
        while not last:
            last = done.wait(POLL)
            with contextlib.suppress(Unreachable):  # the progress says why; the next reading asks again
                read(states(context.suite))

    read(first)
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        yield
    finally:
        done.set()
        watcher.join()


def run(context):
    listed = states(context.suite)
    with watched(context, listed):
        if "start" in context.interrupted and any(c["state"] != "stopped" for c in listed.values()):
            if not started(until(lambda: states(context.suite), started, WAIT)):
                return [NOT_STARTED]
        elif not started(listed):
            context.suite.start()
        return problems(until(lambda: states(context.suite), lambda now: not problems(now), WAIT))


def check(context):
    return problems(states(context.suite))
