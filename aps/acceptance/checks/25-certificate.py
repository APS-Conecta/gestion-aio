#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Our Caddy image gets the suite's certificate through the name service's DNS challenge with no inbound port: the
machine sits behind the runner's NAT, out of the internet's reach. A test build points the proxy at Let's Encrypt's
staging service (its DNS has no answer for the production one); a release's candidate gets a real certificate from
the production one. Either way the certificate the suite serves at its name verifies against the suite's roots alone
(browser.py: the staging roots, or the system's real roots), and a browser that trusts those alone opens the suite at
its name, with the wizard closed at «Listo» (17-install checks what its page says then about this computer and the
router). Nextcloud's trusted certificates list the ISP's intermediate, from the copy the suite ships."""

import json
import os
import socket
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
from browser import chromium, occ, suite_name, tls  # noqa: E402
from playwright.sync_api import Error, expect  # noqa: E402
from steps.certificates import INTERMEDIATE  # noqa: E402

address = os.environ["ADDRESS"]
evidence = Path(os.environ["EVIDENCE"])
name = suite_name()


def issuer():
    """The issuer of the certificate the suite serves for its name at the machine's address, which must verify for that
    name against the suite's roots alone."""
    with socket.create_connection((address, 443), timeout=30) as raw, \
            tls().wrap_socket(raw, server_hostname=name) as verified:
        return ", ".join(f"{key}={value}" for field in verified.getpeercert()["issuer"] for key, value in field)


for _ in range(72):
    try:
        certificate = issuer()
        break
    except OSError as error:  # no answer yet, or a certificate the suite's roots do not verify for the name
        reason = error
        time.sleep(10)
else:
    sys.exit(f"{name} at {address} served no certificate that the suite's roots verify within 12 minutes: {reason}")
print(f"{name} at {address}: a certificate the suite's roots verify, issued by {certificate}")
if "Let's Encrypt" not in certificate:
    sys.exit(f"{name}'s certificate comes from {certificate}, not from Let's Encrypt")

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

trusted = [certificate["name"] for certificate in json.loads(occ("security:certificates --output=json"))]
print(f"Nextcloud's trusted certificates: {', '.join(trusted)}")
if INTERMEDIATE.name not in trusted:
    sys.exit(f"Nextcloud does not trust {INTERMEDIATE.name}, the ISP's intermediate the suite ships")
