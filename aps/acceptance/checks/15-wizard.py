#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one-time link the one command printed opens the wizard in a browser that trusts the wizard's certificate
because its fingerprint is the printed one. The operator reaches «Centro», chooses the centre and reaches
«Dirección», which suggests the centre's name folded and hyphenated. The fixed test address is registered there
with the APS deSEC test account through the wizard's API on the machine, the account's email and password on
stdin only, and the page shows the address registered."""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser import TEST_NAME, chromium, machine, verified, wizard_link  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

missing = [name for name in ("DESEC_TEST_EMAIL", "DESEC_TEST_PASSWORD") if not os.environ.get(name)]
if missing:
    sys.exit(f"the repository secret {' and '.join(missing)} is not set: the APS deSEC test account cannot "
             f"register {TEST_NAME}.dedyn.io")
link, printed = wizard_link(Path(os.environ["CONSOLE"]).read_text(encoding="utf-8"))
url = urlsplit(link)
if url.hostname != os.environ["ADDRESS"]:
    sys.exit(f"the printed link points at {url.hostname}, not at the machine's address {os.environ['ADDRESS']}")
evidence = Path(os.environ["EVIDENCE"])


def curl_config(session, body):
    """A curl config for POST /api/address on the machine: read from stdin, never on a command line."""
    quoted = json.dumps(body).replace("\\", "\\\\").replace('"', '\\"')
    return "\n".join([f'url = "https://127.0.0.1:{url.port}/api/address"',
                      'cacert = "/var/lib/aps-conecta/provisionador/tls.crt"',
                      f'header = "Cookie: __Host-session={session}"', f'json = "{quoted}"', ""])


with chromium(pinned=[verified(url.hostname, url.port, printed)]) as browser:
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(link)
    expect(page.get_by_role("heading", name="Centro")).to_be_visible()
    page.fill("#search [name=name]", "Carlos Ibáñez")
    page.get_by_role("button", name=re.compile("^Centro de Salud Familiar Carlos Ibáñez")).click()
    page.click("#confirm")
    expect(page.get_by_role("heading", name="Dirección")).to_be_visible()
    expect(page.locator("#address-name")).to_have_value(re.compile(r"^cesfam-carlos-ibanez(-\d)?$"), timeout=30_000)
    print(f"«Dirección» suggests {page.input_value('#address-name')}.dedyn.io")
    page.screenshot(path=evidence / "direccion.png")

    session = next(c["value"] for c in page.context.cookies() if c["name"] == "__Host-session")
    body = {"name": TEST_NAME, "email": os.environ["DESEC_TEST_EMAIL"], "password": os.environ["DESEC_TEST_PASSWORD"]}
    answer = json.loads(machine("sudo curl --silent --show-error --config -", curl_config(session, body)))
    if "address" not in answer:
        sys.exit(f"the wizard did not register {TEST_NAME}.dedyn.io: {answer.get('error')}")
    page.reload()
    expect(page.locator("#address-status")).to_have_text(f"Dirección registrada: https://{TEST_NAME}.dedyn.io",
                                                         timeout=30_000)
    page.screenshot(path=evidence / "registrada.png")
    print(f"«Dirección» registered {TEST_NAME}.dedyn.io with the APS deSEC test account")
