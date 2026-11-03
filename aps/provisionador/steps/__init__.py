# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The install steps, one module each; runner.py finds them here and runs each once the steps it needs are green.

A step module, whose name is its id, declares:
  NAME            its Spanish name, as the progress and the red list show it
  NEEDS           the ids of the steps that must be green before it runs
  run(context)    its work, idempotent; it returns what went wrong as Spanish reasons, which the red list shows, and
                  None or [] once the work is done: no step reports success without doing its work
  check(context)  optional, its part of the end-of-install check, which runs once every step is green: the Spanish
                  differences between the running suite and what the step declared, None or [] when it is clean
Both answer the same way; only the suite port raises, Unreachable with its Spanish reason, and the runner turns that
into the step's reason too.
The context holds the suite port (`suite`), the centre (`centre`), its claimed name (`name`), the server's inside
address (`inside`), the folder the Provisionador keeps its files in (`folder`), the ids of the steps a
Provisionador restart cut short (`interrupted`) and of the optional apps the operator ticked on «Revisar»
(`optional`). A new step is a new module: no other file lists the steps.
"""

import json
import time

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
    """Import the settings with occ; what Nextcloud then does not hold, as `differences` answers it."""
    suite.occ("config:import", stdin=json.dumps(settings))
    return differences(suite, settings)
