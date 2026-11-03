# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The install steps, one module each; runner.py finds them here and runs each once the steps it needs are green.

A step module, whose name is its id, declares:
  NAME            its Spanish name, as the progress and the red list show it
  NEEDS           the ids of the steps that must be green before it runs
  run(context)    its work, idempotent; it raises Unreachable, whose Spanish message the red list shows, when the
                  suite does not do what was asked: no step reports success without doing its work
  check(context)  optional, its part of the end-of-install check, which runs once every step is green: the Spanish
                  differences between the running suite and what the step declared, none when it is clean
The context holds the suite port (`suite`), the centre (`centre`), its claimed name (`name`), the server's inside
address (`inside`), the folder the Provisionador keeps its files in (`folder`) and the ids of the steps a
Provisionador restart cut short (`interrupted`). A new step is a new module: no other file lists the steps.
"""

import json
import time

from suite import Unreachable

POLL = 1  # seconds between two readings of the suite while a step waits for it
DIFFERS = ("Nextcloud no conserva estos ajustes: {}. Presione «Reintentar»; si vuelve a ocurrir, contacte a APS "
           "Conecta.")


def until(read, done, wait):
    """`read()` again until `done` accepts what it answers or `wait` seconds pass; its last answer."""
    deadline = time.monotonic() + wait
    while not done(answer := read()) and time.monotonic() < deadline:
        time.sleep(POLL)
    return answer


def differences(suite, settings):
    """Of Nextcloud's settings, {"system": {key: value}, "apps": {app: {key: value}}}, those it does not hold, as one
    Spanish reason in a list, or none."""
    held = json.loads(suite.occ("config:list"))
    wrong = [key for key, value in settings.get("system", {}).items() if held["system"].get(key) != value]
    wrong += [f"{app}.{key}" for app, values in settings.get("apps", {}).items()
              for key, value in values.items() if held["apps"].get(app, {}).get(key) != value]
    return [DIFFERS.format(", ".join(wrong))] if wrong else []


def configure(suite, settings):
    """Import the settings with occ, then make sure Nextcloud holds them."""
    suite.occ("config:import", stdin=json.dumps(settings))
    for reason in differences(suite, settings):
        raise Unreachable(reason)
