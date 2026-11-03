#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The suite's reverse proxy gets its certificate through deSEC's DNS challenge with no inbound port: the machine
sits behind the runner's NAT, out of the internet's reach, and its DNS has no answer for Let's Encrypt's production
service, so the proxy is pointed at the staging one. A browser that adds trust for the staging roots alone opens
the suite at its free domain. The wizard's browser-side check then says this computer opens that address and,
behind a resolver that drops the answer, shows the router change in Spanish."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser import chromium, machine, verified, wizard_link  # noqa: E402
from playwright.sync_api import Error, expect  # noqa: E402

address = os.environ["ADDRESS"]
evidence = Path(os.environ["EVIDENCE"])
domain = json.loads(machine("sudo docker exec nextcloud-aio-mastercontainer cat "
                            "/mnt/docker-aio-config/data/configuration.json")).get("domain")
if not domain:
    sys.exit("the suite has no free domain: «Dirección» registered none")

# Once the proxy runs, its Caddyfile names the staging service and the proxy reloads it.
machine("sudo bash -s", r"""set -euo pipefail
for _ in $(seq 60); do docker top nextcloud-aio-caddy 2> /dev/null | grep -q 'caddy run' && break; sleep 5; done
docker exec nextcloud-aio-caddy grep -q acme-staging /Caddyfile && exit 0
[ "$(docker exec nextcloud-aio-caddy head -n 1 /Caddyfile)" = "{" ]
docker exec nextcloud-aio-caddy sh -c 'awk "NR == 1 {print; print \"acme_ca https://acme-staging-v02.api.letsencrypt.org/directory\"; next} 1" /Caddyfile > /tmp/Caddyfile && cat /tmp/Caddyfile > /Caddyfile'
docker exec nextcloud-aio-caddy caddy reload --config /Caddyfile --adapter caddyfile
""")


def issuer():
    served = subprocess.run(["openssl", "s_client", "-connect", f"{address}:443", "-servername", domain],
                            input="", capture_output=True, text=True, timeout=30, check=False).stdout
    return subprocess.run(["openssl", "x509", "-noout", "-issuer"], input=served, capture_output=True, text=True,
                          check=False).stdout.strip()


for _ in range(72):
    if "(STAGING)" in issuer():
        break
    time.sleep(10)
print(f"{domain} at {address}: {issuer() or 'no certificate'}")
if "(STAGING)" not in issuer():
    sys.exit(f"Let's Encrypt's staging service issued no certificate for {domain} within 12 minutes")

with chromium() as browser:
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        response = page.goto(f"https://{domain}/login")
    except Error as error:
        sys.exit(f"the browser does not open https://{domain}: {error.message.splitlines()[0]}")
    page.screenshot(path=evidence / "suite.png")
    print(f"https://{domain}/login: {response.status}")
    if not response.ok:
        sys.exit(f"https://{domain}/login answers {response.status}")

# The wizard on «Dirección», in a browser on a network whose resolver answers the domain and in one whose resolver
# drops the answer.
for name, args, shown in (
        ("reach", [], f"Este equipo abre https://{domain} correctamente."),
        ("router", [f"--host-resolver-rules=MAP {domain} ~NOTFOUND"],
         f"configure el router para que entregue por DHCP como servidor DNS la dirección {address}")):
    link, printed = wizard_link(machine("sudo python3 /opt/aps-conecta/gestion/provisionador/provisionador.py link"))
    url = urlsplit(link)
    with chromium(pinned=[verified(url.hostname, url.port, printed)], args=args) as browser:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(link)
        expect(page.locator("#reach")).to_contain_text(shown, timeout=60_000)
        page.screenshot(path=evidence / f"{name}.png")
        print(f"{name}: {page.inner_text('#reach')}")
