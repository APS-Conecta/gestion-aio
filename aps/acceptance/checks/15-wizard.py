#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one-time link the one command printed opens the wizard in a browser that trusts the wizard's certificate
because its fingerprint is the printed one. The operator reaches «Centro» and chooses a centre, picked by the run
so concurrent runs claim different names, or a release's RELEASE_DEIS_CODE; «Dirección» shows the centre's name from
the slug table, under the name service the build uses (a test build the CI deployment, a release production), with
nothing to edit, claims it by itself and says so. The claim's credential is stored root-only on the machine (run.sh
masks it). The DEIS code goes to $GITHUB_OUTPUT before the claim, so the run releases it."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
import deis  # noqa: E402
from browser import TEST_BUILD, chromium, machine, verified, wizard_link  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

NAME_SERVICE = ("https://gestion-nombres-ci.apsconecta.workers.dev" if TEST_BUILD
                else "https://gestion-nombres.apsconecta.workers.dev")
services = json.loads(machine("cat /opt/aps-conecta/gestion/services.json"))
if services["url"] != NAME_SERVICE:
    sys.exit(f"{os.environ['TAG']} claims names from {services['url']}, not from {NAME_SERVICE}")
register = deis.Register(deis.newest(REPO / "aps" / "deis"))
slugs = deis.slugs(register.rows)
codes = sorted(slugs)
if TEST_BUILD:
    turn = int(os.environ["GITHUB_RUN_ID"]) + int(os.environ["GITHUB_RUN_ATTEMPT"]) + len(codes) // 2
    code = codes[turn % len(codes)]
else:  # the code the owner set aside for releases, whose name no establishment uses
    code = os.environ.get("RELEASE_DEIS_CODE", "")
    if code not in slugs:
        sys.exit("a release claims the production name of RELEASE_DEIS_CODE, the repository variable the owner sets: "
                 + (f"{code} is not a code of the DEIS register" if code else "it is not set"))
row, name = register.by_code[code], f"{slugs[code]}.{services['suffix']}"

link, printed = wizard_link(Path(os.environ["CONSOLE"]).read_text(encoding="utf-8"))
url = urlsplit(link)
if url.hostname != os.environ["ADDRESS"]:
    sys.exit(f"the printed link points at {url.hostname}, not at the machine's address {os.environ['ADDRESS']}")
evidence = Path(os.environ["EVIDENCE"])

with chromium(pinned=[verified(url.hostname, url.port, printed)]) as browser:
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(link)
    expect(page.get_by_role("heading", name="Centro")).to_be_visible()
    page.select_option("#search [name=region]", row["region_codigo"])
    page.select_option("#search [name=comuna]", row["comuna_codigo"])
    page.fill("#search [name=name]", row["nombre"])
    page.click(f'#results button[data-code="{code}"]')
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"code={code}\n")
    page.click("#confirm")
    expect(page.get_by_role("heading", name="Dirección")).to_be_visible()
    expect(page.locator("#address-name")).to_have_text(name)
    expect(page.locator("#direccion input")).to_have_count(0)
    expect(page.locator("#address-status")).to_have_text(f"Dirección asignada: https://{name}", timeout=180_000)
    page.screenshot(path=evidence / "direccion.png")

mode = machine("sudo stat -c '%U %a' /var/lib/aps-conecta/name-service.json").strip()
if mode != "root 600":
    sys.exit(f"the claim is stored with owner and mode {mode}, not root 600")
print(f"«Dirección» claimed {name} for {row['nombre']} with nothing asked; the claim is stored root-only")
