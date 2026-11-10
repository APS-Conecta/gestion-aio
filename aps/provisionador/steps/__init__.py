# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The install steps, one module each; runner.py finds them here and runs each once the steps it needs are green.

A step module, whose name is its id, declares:
  NAME            its Spanish name, as the progress and the red list show it
  NEEDS           the ids of the steps that must be green before it runs
  OWNED           optional, True when the suite's own pages own what the step made once «Listo» is reached: an upgrade
                  of the server keeps such a step green, never re-running it nor re-checking it
  run(context)    its work, idempotent; it returns what went wrong as Spanish reasons, which the red list shows, and
                  None or [] once the work is done: no step reports success without doing its work
  check(context)  optional, its part of the end-of-install check, which runs once every step is green: the Spanish
                  differences between what the step declared and what holds now (the running suite, and for people
                  also what «Equipos» declares now), None or [] when it is clean
Both answer the same way; only the suite port raises, Unreachable with its Spanish reason, and the runner turns that
into the step's reason too.
The context holds the suite port (`suite`), the centre (`centre`), its claimed name (`name`), the server's inside
address (`inside`), the folder the Provisionador keeps its files in (`folder`), the ids of the steps a Provisionador
restart cut short (`interrupted`) and of the optional apps the operator ticked on «Revisar» (`optional`), whether «IA
local» is on (`ai`), and the teams declared on «Equipos» (`teams`, as teams.py names them), the people its spreadsheet
loads (`people`, as spreadsheet.py reads them) and the usernames retired there (`retired`), as they were when «Instalar»
or «Reintentar» was pressed; `declared()` answers those people and usernames as «Equipos» declares them now, and
`log(text)` adds our own Spanish line, never a secret nor what occ or AIO printed, to the install's log. A new step is a
new module: no other file lists the steps.
A step that configures an app gets it through `steps.apps.ensure`
rather than NEEDS "apps".
"""

import json
import time

from suite import HIDDEN

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
    Spanish reason in a list, or none. An app setting occ config:list hides as sensitive, such as theming's url, is read
    with config:app:get."""
    held = json.loads(suite.occ("config:list"))

    def app_value(app, key):
        value = held["apps"].get(app, {}).get(key)
        return json.loads(suite.occ("config:app:get", app, key, "--output=json")) if value == HIDDEN else value

    wrong = [key for key, value in settings.get("system", {}).items() if held["system"].get(key) != value]
    wrong += [f"{app}.{key}" for app, values in settings.get("apps", {}).items()
              for key, value in values.items() if app_value(app, key) != value]
    return [DIFFERS.format(", ".join(wrong))] if wrong else []


def configure(suite, settings):
    """Import the settings with occ; what Nextcloud then does not hold, as `differences` answers it."""
    suite.occ("config:import", stdin=json.dumps(settings))
    return differences(suite, settings)
