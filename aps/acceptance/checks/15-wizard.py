#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one-time link the one command printed opens the wizard in a browser that trusts the wizard's certificate
because its fingerprint is the printed one. The operator reaches «Centro», chooses the centre and reaches
«Dirección», which suggests the centre's name folded and hyphenated. The APS deSEC test account's fixed address is
registered there through the wizard's API on the machine, the account's email and password on stdin only, and the
page shows the address registered. deSEC lets an account hold one dedyn.io name, so the fixed name is the one the
account holds (on a fresh account, the one it then keeps)."""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser import chromium, machine, verified, wizard_link  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

missing = [name for name in ("DESEC_TEST_EMAIL", "DESEC_TEST_PASSWORD") if not os.environ.get(name)]
if missing:
    sys.exit(f"the repository secret {' and '.join(missing)} is not set: the APS deSEC test account cannot register "
             "its address")


def desec(path, body=None, token=None):
    """deSEC's API from the runner; the account's credentials and token are never printed."""
    request = urllib.request.Request(f"https://desec.io/api/v1/{path}", data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Token {token}"} if token else {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or "null")


def with_account(use):
    """use(token) for the APS deSEC test account, between a login and a logout."""
    token = desec("auth/login/", {"email": os.environ["DESEC_TEST_EMAIL"],
                                  "password": os.environ["DESEC_TEST_PASSWORD"]})["token"]
    print(f"::add-mask::{token}", flush=True)
    try:
        return use(token)
    finally:
        desec("auth/logout/", {}, token)


def desec_answer(token):
    """deSEC's own answer to registering the test name for the account, which upstream's message hides."""
    try:
        desec("domains/", {"name": f"{TEST_NAME}.dedyn.io"}, token)
        return "201 Created"
    except urllib.error.HTTPError as error:
        return f"HTTP {error.code} {error.read().decode()}"


held = with_account(lambda token: [d["name"][:-len(".dedyn.io")] for d in desec("domains/", token=token)
                                   if d["name"].endswith(".dedyn.io")])
TEST_NAME = held[0] if held else "aps-gestion-aio-ci"
print(f"the APS deSEC test account holds {', '.join(held) or 'no dedyn.io name'}; CI registers {TEST_NAME}.dedyn.io")
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
        sys.exit(f"the wizard did not register {TEST_NAME}.dedyn.io: {answer.get('error')} (deSEC answers the test "
                 f"account itself: {with_account(desec_answer)})")
    page.reload()
    expect(page.locator("#address-status")).to_have_text(f"Dirección registrada: https://{TEST_NAME}.dedyn.io",
                                                         timeout=30_000)
    page.screenshot(path=evidence / "registrada.png")
    print(f"«Dirección» registered {TEST_NAME}.dedyn.io with the APS deSEC test account")
