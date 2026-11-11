#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one-time link the one command printed opens the wizard in a browser that trusts the wizard's certificate because
its fingerprint is the printed one. The operator reaches «Centro», under a header that shows the suite's motto, on a
page in the brand fonts with no console error and no content-security-policy violation. A test build's run chooses a
centre there, picked so concurrent runs claim different names under the name service's CI deployment, and writes its
DEIS code to $GITHUB_OUTPUT before the claim, so the run releases it. A release's run never holds a real establishment's
production name: before the one command it claimed the name of the register's test establishment with APS's token
(aps-acceptance.yml). The check hands that claim to the machine root-only, where the suite port takes it instead of
claiming, sends the test establishment, which «Centro» never lists, to the wizard's API, and the page resumes at
«Dirección». «Dirección» shows the centre's name from the slug table, with nothing to edit, claims it by itself and says
so. The claim's credential is stored root-only on the machine (run.sh masks it)."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
import brand  # noqa: E402
import deis  # noqa: E402
from browser import TEST_BUILD, chromium, faults, fonts, machine, verified, wizard_link  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

NAME_SERVICE = ("https://gestion-nombres-ci.apsconecta.workers.dev" if TEST_BUILD
                else "https://gestion-nombres.apsconecta.workers.dev")
services = json.loads(machine("cat /opt/aps-conecta/gestion/services.json"))
if services["url"] != NAME_SERVICE:
    sys.exit(f"{os.environ['TAG']} claims names from {services['url']}, not from {NAME_SERVICE}")
register = deis.Register(deis.newest(REPO / "aps" / "deis"))
slugs = deis.slugs(register.rows)
if TEST_BUILD:
    codes = sorted(r["codigo"] for r in register.listed)
    turn = int(os.environ["GITHUB_RUN_ID"]) + int(os.environ["GITHUB_RUN_ATTEMPT"]) + len(codes) // 2
    code = codes[turn % len(codes)]
else:  # the run's claim, which holds the credential: never printed
    claim = Path(os.environ["RUNNER_TEMP"], "acceptance", "claim.json").read_text(encoding="utf-8")
    code = json.loads(claim)["username"]
row, name = register.by_code[code], f"{slugs[code]}.{services['suffix']}"

link, printed = wizard_link(Path(os.environ["CONSOLE"]).read_text(encoding="utf-8"))
url = urlsplit(link)
if url.hostname != os.environ["ADDRESS"]:
    sys.exit(f"the printed link points at {url.hostname}, not at the machine's address {os.environ['ADDRESS']}")
evidence = Path(os.environ["EVIDENCE"])
# The wizard's own request for a centre, from the page and its session.
CENTRE = """code => fetch('/api/centre', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                      body: JSON.stringify({code})}).then(r => r.status)"""


with chromium(pinned=[verified(url.hostname, url.port, printed)]) as browser:
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    found = faults(page)
    page.goto(link)
    expect(page.get_by_role("heading", name="Centro")).to_be_visible()
    expect(page.get_by_role("banner").get_by_text(brand.MOTTO)).to_be_visible()
    page.wait_for_timeout(3000)  # the map draws its tiles
    page.screenshot(path=evidence / "centro.png")
    if TEST_BUILD:
        page.select_option("#search [name=region]", row["region_codigo"])
        page.select_option("#search [name=comuna]", row["comuna_codigo"])
        page.fill("#search [name=name]", row["nombre"])
        page.click(f'#results button[data-code="{code}"]')
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"code={code}\n")
        page.click("#confirm")
    else:
        machine("sudo sh -c 'umask 077 && cat > /var/lib/aps-conecta/name-service.json'", claim)
        status = page.evaluate(CENTRE, code)
        if status != 200:
            sys.exit(f"the wizard's API answers {status} to the test establishment")
        page.reload()
    expect(page.get_by_role("heading", name="Dirección")).to_be_visible()
    expect(page.locator("#address-name")).to_have_text(name)
    expect(page.locator("#direccion input")).to_have_count(0)
    expect(page.locator("#address-status")).to_have_text(f"Dirección asignada: https://{name}", timeout=180_000)
    page.screenshot(path=evidence / "direccion.png")
    loaded = fonts(page)

print(f"the wizard loaded the fonts {loaded}; its console errors and policy violations: {found}")
if found:
    sys.exit(f"the wizard logged errors or content-security-policy violations: {found}")
if not {"Fraunces", "Nunito Sans"} <= set(loaded):
    sys.exit(f"the wizard did not load the brand fonts, only {loaded}")

mode = machine("sudo stat -c '%U %a' /var/lib/aps-conecta/name-service.json").strip()
if mode != "root 600":
    sys.exit(f"the claim is stored with owner and mode {mode}, not root 600")
print(f"«Dirección» claimed {name} for {row['nombre']} with nothing asked; the claim is stored root-only")
