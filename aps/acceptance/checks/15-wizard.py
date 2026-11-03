#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one-time link the one command printed opens the wizard in a browser that trusts the wizard's certificate
because its fingerprint is the printed one. The operator reaches «Centro» and chooses a centre, picked by the run
so concurrent runs claim different names; «Dirección» shows the centre's name from the slug table, under the CI
name service this test build uses, with nothing to edit, claims it by itself and says so. The claim's credential
is stored root-only on the machine. The DEIS code goes to $GITHUB_OUTPUT before the claim, so the run releases it."""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
import deis  # noqa: E402
from browser import chromium, machine, verified, wizard_link  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

services = json.loads(machine("cat /opt/aps-conecta/gestion/services.json"))
if services["url"] != "https://gestion-nombres-ci.apsconecta.workers.dev":
    sys.exit(f"the test build claims names from {services['url']}, not from the name service's CI deployment")
register = deis.Register(deis.newest(REPO / "aps" / "deis"))
slugs = deis.slugs(register.rows)
codes = sorted(slugs)
code = codes[(int(os.environ["GITHUB_RUN_ID"]) + int(os.environ["GITHUB_RUN_ATTEMPT"]) + len(codes) // 2) % len(codes)]
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
    credential = json.loads(machine("sudo cat /var/lib/aps-conecta/name-service.json"))
    print(f"::add-mask::{credential['password']}", flush=True)
    page.screenshot(path=evidence / "direccion.png")

owner = machine("sudo stat -c '%U %a' /var/lib/aps-conecta/name-service.json").strip()
if (credential["name"], owner) != (name, "root 600"):
    sys.exit(f"the stored claim is for {credential['name']}, owned {owner}; expected {name}, root 600")
print(f"«Dirección» claimed {name} for {row['nombre']} with nothing asked; its credential is root-only")
