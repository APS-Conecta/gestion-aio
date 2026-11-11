# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""AIO's start: it pulls, creates and starts every container, Apache last, then each container answers in turn.

The step goes by AIO's own reading of the containers. Once Apache runs, AIO started the suite (and closed its login), so
the step does not press start again. After a Provisionador restart cut the step short, AIO goes on with the start it was
sent; while it does, the step waits for it instead of sending another one.

Every reading the step makes goes through one reader, which writes to the install's log each container's state as first
read and whenever it changes; a thread reads only while AIO's start runs, and the step reads again once it is joined.
While AIO's start answers, the reader also writes each container whose image AIO begins to download, as the answer says.
Once the reader wrote nothing for HEARTBEAT seconds while a container does not run, it says how the start goes: until
AIO started every container, how many run; from then on, which do not.
"""

import contextlib
import threading
import time

from steps import POLL, until
from steps.components import named
from suite import NOT_STARTED, Unreachable

NAME = "Inicio de la suite"
NEEDS = ("components", "backups", "storage")
APACHE = "nextcloud-aio-apache"
WAIT = 15 * 60  # seconds: as long as AIO's start may take
HEARTBEAT = 2 * 60  # seconds the log may stay silent while a container does not run
NOT_RUNNING = ("Estos contenedores de la suite no quedaron en ejecución: {}. Presione «Reintentar»; si vuelve a "
               "ocurrir, contacte a APS Conecta.")
CONTAINER_LINE = "Contenedor «{}»: {}."
STATES = {"stopped": "en espera", "starting": "iniciando", "running": "en ejecución"}  # as the progress shows them
DOWNLOADING = "AIO descarga su imagen"
STARTING = NAME + ": AIO descarga e inicia los contenedores; {} de {} en ejecución."
STARTED = NAME + ": AIO inició todos los contenedores."
LATE = NAME + ": aún no están en ejecución: {}."


def states(suite):
    return {c["id"]: c for c in suite.containers()}


def started(listed):
    return listed.get(APACHE, {}).get("state") == "running"


def late(listed):
    """The containers that do not run, by the names the operator reads."""
    return [named(c) for c in listed.values() if c["state"] != "running"]


def problems(listed):
    return [NOT_RUNNING.format(", ".join(names))] if (names := late(listed)) else []


class Reader:
    """The step's readings of the containers, with what they tell the install's log."""

    def __init__(self, context):
        self.context, self.seen, self.answered, self.at = context, {}, False, time.monotonic()

    def log(self, text):
        self.context.log(text)
        self.at = time.monotonic()

    def __call__(self):
        listed = states(self.context.suite)
        for id_, c in listed.items():
            if self.seen.get(id_, {}).get("state") != c["state"]:
                self.log(CONTAINER_LINE.format(named(c), STATES[c["state"]]))
        self.seen = listed
        if (waiting := late(listed)) and time.monotonic() - self.at >= HEARTBEAT:
            self.log(LATE.format(", ".join(waiting)) if self.answered
                     else STARTING.format(len(listed) - len(waiting), len(listed)))
        return listed

    def downloading(self, container):
        """AIO begins to download the container's image."""
        self.log(CONTAINER_LINE.format(named(container), DOWNLOADING))

    def started(self):
        """AIO started every container: from now on a silent log names those that do not run yet."""
        self.answered = True
        self.log(STARTED)


@contextlib.contextmanager
def reading(read):
    """`read` every POLL seconds while the block runs, joined before it ends."""
    done = threading.Event()

    def poll():
        while not done.wait(POLL):
            with contextlib.suppress(Unreachable):  # the progress says why; the next reading asks again
                read()

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()


def run(context):
    read = Reader(context)
    listed = read()
    if started(listed):
        read.started()
    elif "start" in context.interrupted and any(c["state"] != "stopped" for c in listed.values()):
        if not started(until(read, started, WAIT)):
            return [NOT_STARTED]
        read.started()
    else:
        with reading(read):
            context.suite.start(read.downloading)
        read.started()
    return problems(until(read, lambda now: not problems(now), WAIT))


def check(context):
    return problems(states(context.suite))
