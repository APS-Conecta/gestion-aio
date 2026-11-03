#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Our Caddy image gets the suite's certificate through the name service's DNS challenge with no inbound port: the
machine sits behind the runner's NAT, out of the internet's reach, and the test build points the proxy at Let's
Encrypt's staging service (its DNS has no answer for the production one). A browser that adds trust for the staging
roots alone opens the suite at its name, with the wizard closed at «Listo» (17-install checks what its page says
then about this computer and the router)."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser import chromium, machine  # noqa: E402
from playwright.sync_api import Error, expect  # noqa: E402

address = os.environ["ADDRESS"]
evidence = Path(os.environ["EVIDENCE"])
name = json.loads(machine("sudo docker exec nextcloud-aio-mastercontainer cat "
                          "/mnt/docker-aio-config/data/configuration.json")).get("domain")
if not name:
    sys.exit("the suite has no name: «Dirección» claimed none")


def served():
    """The issuer and names of the certificate the suite serves for its name at the machine's address."""
    pem = subprocess.run(["openssl", "s_client", "-connect", f"{address}:443", "-servername", name],
                         input="", capture_output=True, text=True, timeout=30, check=False).stdout
    return subprocess.run(["openssl", "x509", "-noout", "-issuer", "-ext", "subjectAltName"], input=pem,
                          capture_output=True, text=True, check=False).stdout.strip()


for _ in range(72):
    if "(STAGING)" in served():
        break
    time.sleep(10)
certificate = served()
print(f"{name} at {address}: {certificate or 'no certificate'}")
if "(STAGING)" not in certificate or f"DNS:{name}" not in certificate:
    sys.exit(f"Let's Encrypt's staging service issued no certificate for {name} within 12 minutes")

with chromium() as browser:
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        response = page.goto(f"https://{name}/login")
    except Error as error:
        sys.exit(f"the browser does not open https://{name}: {error.message.splitlines()[0]}")
    page.screenshot(path=evidence / "suite.png")
    print(f"https://{name}/login: {response.status}")
    if not response.ok:
        sys.exit(f"https://{name}/login answers {response.status}")
    expect(page.locator("input[type=password]")).to_be_visible()
