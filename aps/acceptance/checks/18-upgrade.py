#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one command's upgrade of the installed previous release: rerunning it moves the server to the release under
test. The bundle and the mastercontainer move to it, the suite stops and every install step but the people's runs
again. A fresh link opens the wizard at the same install, whose log names the upgrade, and the operator looks at
«Equipos». Where it rejects the people spreadsheet the previous release kept, they download the workbook the upgraded
wizard offers, fill «Personal» with the install's people, with their cargos, and upload it, as 17-install does; once
the other steps are done, the people's step alone is red, and «Reintentar» applies them. Where «Equipos» still loads the
kept spreadsheet, nothing is uploaded and the people's step stays green throughout, for the Users page owns the people.
«Listo» follows as at the first install: the suite keeps its name and address, the passwords file is handed over again
and the wizard closes."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
import spreadsheet  # noqa: E402
from browser import chromium, machine, provisionador_file, provisionador_state, suite_name, verified, wizard_link  # noqa: E402
from browser import XLSX, people_workbook, upload_people  # noqa: E402
from playwright.sync_api import expect  # noqa: E402
from steps.passwords import FILE  # noqa: E402
from steps.people import NAME as ACCOUNTS  # noqa: E402

LISTO_WITHIN = 20  # minutes: the release's images alone can take several to download


def step_states(page):
    """Each step on «Revisar», by its Spanish name, with its state."""
    return dict(page.eval_on_selector_all("#steps li",
                                           "items => items.map(i => [i.querySelector('strong').textContent, i.dataset.state])"))


def reload_people(page, state):
    """«Equipos» after the upgrade, then back to «Revisar»: where it rejects the people spreadsheet the previous release
    kept, the workbook the upgraded wizard offers, filled with the install's people, uploaded; whether it had to."""
    page.click(".step-link[data-step=equipos]")
    expect(page.get_by_role("heading", name="Equipos")).to_be_visible()
    rejected = page.locator("#people-errors li").all_inner_texts()
    if rejected:
        print(f"«Equipos» rejects the spreadsheet the previous release kept: {rejected}")
        page.screenshot(path=evidence / "equipos-rechaza.png")
        workbook = people_workbook(page)
        upload_people(page, spreadsheet.FILE, XLSX, workbook,
                      spreadsheet.read(spreadsheet.rows(workbook), state.get("teams", []), state["centre"]["type"])[0])
        page.screenshot(path=evidence / "equipos.png")
    page.click("#teams-continue")
    expect(page.get_by_role("heading", name="Revisar")).to_be_visible()
    return bool(rejected)


tag = os.environ["TAG"]
version = machine("cat /opt/aps-conecta/gestion/VERSION").strip()
if version != tag:
    sys.exit(f"the machine holds the bundle of {version}, not {tag}'s upgrade")
name, state = suite_name(), provisionador_state()
# the marking the one command's upgrade leaves, which it sets only over a finished, closed install
if state.get("install", {}).get("upgrade") != tag:
    sys.exit(f"the wizard's state records no upgrade to {tag}: did the previous release's install ever finish?")

with tempfile.NamedTemporaryFile() as console:  # one-command.sh masks the fresh link's secret before the check reads it
    subprocess.run([REPO / "aps" / "acceptance" / "one-command.sh", console.name,
                    "sudo python3 /opt/aps-conecta/gestion/provisionador/provisionador.py link"], check=True)
    link, printed = wizard_link(Path(console.name).read_text(encoding="utf-8"))
url = urlsplit(link)
evidence = Path(os.environ["EVIDENCE"])
with chromium(pinned=[verified(url.hostname, url.port, printed)]) as browser:
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(link)
    expect(page.locator("#steps li")).not_to_have_count(0)  # the reconcile runs by itself
    expect(page.locator("#log")).to_contain_text(f"Comienza la actualización a {tag}", timeout=60_000)
    page.screenshot(path=evidence / "actualizacion.png")
    reloaded, retried = reload_people(page, state), False
    deadline = time.monotonic() + LISTO_WITHIN * 60
    while not page.locator("#listo").is_visible():
        states = step_states(page)
        if (reloaded and not retried and page.locator("#install").is_visible()
                and [step for step, done in states.items() if done != "green"] == [ACCOUNTS]):
            page.screenshot(path=evidence / "reintentar.png")
            page.click("#install")  # «Reintentar» applies the people uploaded
            retried = True
        elif not reloaded and states.get(ACCOUNTS) not in (None, "green"):
            page.screenshot(path=evidence / "personal.png")
            sys.exit(f"the upgrade re-ran the people's step: {page.inner_text('#steps')}")
        if time.monotonic() > deadline:
            page.screenshot(path=evidence / "actualizacion-failed.png")
            sys.exit(f"no «Listo» within {LISTO_WITHIN} minutes: {page.inner_text('#install-status')}\n"
                     f"{page.inner_text('#steps')}")
        page.wait_for_timeout(2000)
    expect(page.locator("#suite-address")).to_have_text(f"https://{name}")
    page.screenshot(path=evidence / "listo.png")
    with page.expect_download() as download:
        page.click("#passwords")
    handed = Path(download.value.path()).read_text(encoding="utf-8")
    if page.evaluate("fetch('/api/state').then(r => r.status, () => 'closed')") not in ("closed", 401):
        sys.exit("the wizard's session still works after the upgrade's «Listo»")

if handed != provisionador_file(FILE):
    sys.exit("the passwords file the upgrade handed over is not the machine's copy")
after = provisionador_state()
if after["address"]["name"] != state["address"]["name"]:
    sys.exit(f"the upgrade changed the suite's name: {state['address']['name']} → {after['address']['name']}")
people = "loaded its people again from the workbook it offers" if reloaded else "kept its people"
print(f"the upgrade to {version} reopened the wizard, kept https://{name}, {people} "
      "and closed at «Listo» with the passwords file")
