# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The install step runner: «Instalar» runs every step under steps/ whose needs are green, in an order where each comes
after the steps it needs. A step that fails gets its Spanish reason; its dependents wait, and say for what; a step that
does not need it still runs. «Reintentar» runs the failed and waiting steps again, never a green one. Once every step
is green, the end-of-install check gives, step by step, the differences between what the step declared and what holds
now: the running suite, or for people what «Equipos» declares now. A step whose part differs turns red again. «Listo»
needs every step green and a clean check.

Each step's status is kept in the wizard's state as it changes: pending, running, green, failed or waiting. A step still
running when the Provisionador starts was cut short by a restart, and the install resumes by itself.
"""

import importlib
import pkgutil
import traceback

import steps
from suite import Unreachable

UNEXPECTED = ("La instalación se detuvo por un error inesperado. Presione «Reintentar»; si vuelve a ocurrir, "
              "contacte a APS Conecta e indique el registro del servidor: journalctl -u aps-provisionador")
WAITING = "Espera a que se complete: {}."


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


def begin(state):
    """«Instalar» or «Reintentar»: every step that is not green is to run."""
    with state.change() as s:
        install = s.setdefault("install", {"steps": {}})
        for name in STEPS:
            if install["steps"].get(name, {}).get("status") != "green":
                install["steps"][name] = {"status": "pending"}


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


def record(state, name, status, reasons=()):
    with state.change() as s:
        s["install"]["steps"][name] = {"status": status, **({"reason": " ".join(reasons)} if reasons else {})}


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
    for name, step in STEPS.items():
        if hasattr(step, "check"):
            record(state, name, "running")
            found = problems(step.check, context)
            record(state, name, "failed" if found else "green", found)
    with state.change() as s:  # a step the wizard reopened meanwhile keeps «Listo» away
        s["install"]["listo"] = all(step["status"] == "green" for step in s["install"]["steps"].values())


def progress(install):
    """Each step by its Spanish name, with its status and, when it is failed or waiting, its reason."""
    return [{"name": STEPS[name].NAME, **step} for name, step in install["steps"].items()]
