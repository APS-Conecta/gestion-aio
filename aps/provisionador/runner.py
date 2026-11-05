# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The install step runner: «Instalar» runs every step under steps/ whose needs are green, in an order where each comes
after the steps it needs. A step that fails gets its Spanish reason; its dependents wait, and say for what; a step that
does not need it still runs. «Reintentar» runs the failed and waiting steps again, never a green one. Once every step
is green, the end-of-install check gives, step by step, the differences between what the step declared and what holds
now: the running suite, and for people also what «Equipos» declares now. A step whose part differs turns red again.
«Listo» needs every step green and a clean check.

Each step's status is kept in the wizard's state as it changes: pending, running, green, failed or waiting. A step still
running when the Provisionador starts was cut short by a restart, and the install resumes by itself.

The install's log, «Registro de la instalación», keeps the latest timestamped Spanish lines, which the Provisionador's
journal gets too: each «Instalar», «Reintentar» and resumed install, each status a step is set to, with its reason (by
the runner, or by the wizard when it reopens the people step), the end-of-install check and what it finds, «Listo», and
what a step adds with `context.log`. Only our
own texts go there, with names and reasons the progress shows anyway: never a secret, nor what occ or AIO printed.
"""

import importlib
import pkgutil
import traceback
from datetime import datetime, timezone

import steps
from suite import Unreachable

UNEXPECTED = ("La instalación se detuvo por un error inesperado. Presione «Reintentar»; si vuelve a ocurrir, "
              "contacte a APS Conecta e indique el registro del servidor: journalctl -u aps-provisionador")
WAITING = "Espera a que se complete: {}."
LINES = 200  # the install's log keeps its latest lines
INSTALAR = "«Instalar»: comienza la instalación."
REINTENTAR = "«Reintentar»: se repiten los pasos que no se completaron."
RESUMED = "El Provisionador se reinició y continúa la instalación."
# A step's line in the log for each status it is set to, its reason after it.
STEP_LINE = {"running": "Paso «{}»: en curso.", "green": "Paso «{}»: completado.",
             "failed": "Paso «{}»: no se completó.", "waiting": "Paso «{}»: aún no comienza."}
CHECK = "Comienza la comprobación final de la instalación."
LISTO = "La instalación terminó."


def discover():
    """Every step module, by id, each after the steps it needs; steps of one rank by id."""
    found = {m.name: importlib.import_module(f"steps.{m.name}") for m in pkgutil.iter_modules(steps.__path__)}
    ordered = []
    while len(ordered) < len(found):
        ready = sorted(name for name, step in found.items() if name not in ordered and set(step.NEEDS) <= set(ordered))
        if not ready:
            raise RuntimeError(f"steps that need unknown or circular steps: {sorted(set(found) - set(ordered))}")
        ordered += ready
    return {name: found[name] for name in ordered}


STEPS = discover()


def add_line(install, text):
    """A line in the install's log, which keeps the latest LINES, and in the Provisionador's journal."""
    print(text, flush=True)
    line = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "text": text}
    install["log"] = [*install.get("log", []), line][-LINES:]


def log(state, text):
    """A line in the install's log, as a step adds it with `context.log`."""
    with state.change() as s:
        add_line(s["install"], text)


def begin(state):
    """«Instalar» or «Reintentar»: every step that is not green is to run."""
    with state.change() as s:
        again = "install" in s
        install = s.setdefault("install", {"steps": {}})
        for name in STEPS:
            if install["steps"].get(name, {}).get("status") != "green":
                install["steps"][name] = {"status": "pending"}
        add_line(install, REINTENTAR if again else INSTALAR)


def unfinished(install):
    """The steps a run has not settled yet: pending, or running."""
    return {name for name, step in install["steps"].items() if step["status"] in ("pending", "running")}


def problems(call, context):
    """What a step's run or check found wrong, in Spanish."""
    try:
        return call(context) or []
    except Unreachable as failure:
        return [str(failure)]
    except Exception:  # a defect: the journal gets the traceback, the operator a reason
        traceback.print_exc()
        return [UNEXPECTED]


def set_status(install, name, status, reasons=(), logged=True):
    """A step's status, with its reasons, and, if `logged`, its line in the install's log."""
    install["steps"][name] = {"status": status, **({"reason": " ".join(reasons)} if reasons else {})}
    if logged:
        add_line(install, " ".join([STEP_LINE[status].format(STEPS[name].NAME), *reasons]))


def record(state, name, status, reasons=(), logged=True):
    """`set_status` in the wizard's state. What a step's run or check found lands only while it runs: a step the wizard
    reopened meanwhile keeps the wizard's reason."""
    with state.change() as s:
        if status in ("green", "failed") and s["install"]["steps"][name]["status"] != "running":
            return
        set_status(s["install"], name, status, reasons, logged)


def run(state, context):
    """Run every pending step in order, then, once all are green, the end-of-install check."""
    for name, step in STEPS.items():
        install = state.read()["install"]
        if name not in unfinished(install):
            continue
        status = {n: r["status"] for n, r in install["steps"].items()}
        blocked = [f"«{STEPS[need].NAME}»" for need in step.NEEDS if status[need] != "green"]
        if blocked:
            record(state, name, "waiting", [WAITING.format(", ".join(blocked))])
            continue
        record(state, name, "running")
        found = problems(step.run, context)
        record(state, name, "failed" if found else "green", found)
    if any(r["status"] != "green" for r in state.read()["install"]["steps"].values()):
        return
    log(state, CHECK)
    for name, step in STEPS.items():
        if hasattr(step, "check"):
            record(state, name, "running", logged=False)
            found = problems(step.check, context)
            record(state, name, "failed" if found else "green", found, logged=bool(found))
    with state.change() as s:  # a step the wizard reopened meanwhile keeps «Listo» away
        install = s["install"]
        install["listo"] = all(step["status"] == "green" for step in install["steps"].values())
        if install["listo"]:
            add_line(install, LISTO)


def progress(install):
    """Each step by its Spanish name, with its status and, when it is failed or waiting, its reason."""
    return [{"name": STEPS[name].NAME, **step} for name, step in install["steps"].items()]
