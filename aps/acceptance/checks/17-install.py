#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""«Revisar» and «Instalar», from a fresh link as an operator who comes back to the wizard: «Dirección» goes on to
«Revisar», which summarises the centre's address, the apps and the backups; «Instalar» shows each container's progress
live until «Listo» shows the suite's address. AIO then reports our components on, every other optional one off and
nightly backups to the backup folder. AIO's passphrase never reaches the wizard: no answer the browser got carries it,
and on the machine only root reads the file AIO keeps it in."""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
from browser import chromium, machine, verified, wizard_link  # noqa: E402
from playwright.sync_api import expect  # noqa: E402
from provisionador import BACKUP_FOLDER, BACKUP_TIME  # noqa: E402

APPS = ["Talk", "Euro-Office", "Imaginary", "ClamAV (antivirus)", "Búsqueda de texto completo",
        "HaRP (servidor de aplicaciones externas)"]
CHOSEN = ["Talk", "EuroOffice", "Imaginary", "ClamAV", "Fulltextsearch", "HaRP"]  # their containers, as AIO names them
SWITCHES = {"isTalkEnabled": True, "officeSuite": "eurooffice", "isImaginaryEnabled": True, "isClamavEnabled": True,
            "isFulltextsearchEnabled": True, "isHarpEnabled": True, "isTalkRecordingEnabled": False,
            "isDockerSocketProxyEnabled": False, "isWhiteboardEnabled": False, "aio_community_containers": "caddy",
            "borg_backup_host_location": BACKUP_FOLDER, "borg_remote_repo": ""}
AIO_DATA = "/mnt/docker-aio-config/data"


def aio(file):
    return machine(f"sudo docker exec nextcloud-aio-mastercontainer cat {AIO_DATA}/{file}")


config = json.loads(aio("configuration.json"))
passphrase, name = config.get("password"), config["domain"]
if not passphrase:  # it stays in memory: run.sh masks it in the log
    sys.exit("AIO stored no passphrase")
evidence = Path(os.environ["EVIDENCE"])
link, printed = wizard_link(machine("sudo python3 /opt/aps-conecta/gestion/provisionador/provisionador.py link"))
url = urlsplit(link)

with tempfile.TemporaryDirectory() as tmp, chromium(pinned=[verified(url.hostname, url.port, printed)]) as browser:
    har = Path(tmp, "wizard.har")  # every answer the browser got, bodies included; it stays out of the evidence
    context = browser.new_context(viewport={"width": 1280, "height": 900}, record_har_path=har,
                                  record_har_content="embed")
    page = context.new_page()
    page.goto(link)
    page.click("#continue")
    expect(page.get_by_role("heading", name="Revisar")).to_be_visible()
    expect(page.locator("#summary-address")).to_have_text(f"https://{name}")
    expect(page.locator("#summary-components li")).to_have_text(APPS)
    expect(page.locator("#summary-backups")).to_contain_text(BACKUP_FOLDER)
    page.screenshot(path=evidence / "revisar.png")

    page.click("#install")
    seen = {}  # each container's states, in the order the page showed them
    deadline = time.monotonic() + 25 * 60
    while not page.locator("#listo").is_visible():
        for container, state in page.eval_on_selector_all(
                "#progress li", "items => items.map(i => [i.querySelector('strong').textContent, i.dataset.state])"):
            if seen.setdefault(container, [state])[-1] != state:
                seen[container].append(state)
        if page.locator("#install").is_visible() or time.monotonic() > deadline:
            page.screenshot(path=evidence / "revisar-failed.png")
            sys.exit(f"no «Listo» within 25 minutes: {page.inner_text('#install-status')}")
        if {"running", "stopped"} <= {s[-1] for s in seen.values()} and not (evidence / "progress.png").exists():
            page.screenshot(path=evidence / "progress.png")
        page.wait_for_timeout(2000)
    (evidence / "progress.txt").write_text("".join(f"{c}: {' → '.join(s)}\n" for c, s in seen.items()), encoding="utf-8")
    print("".join(f"{c}: {' → '.join(s)}\n" for c, s in seen.items()), end="")
    expect(page.locator("#suite-address")).to_have_text(f"https://{name}")
    expect(page.locator("#suite-address")).to_have_attribute("href", f"https://{name}")
    page.screenshot(path=evidence / "listo.png")
    context.close()
    carried = passphrase in har.read_text(encoding="utf-8")

unshown = [c for c in CHOSEN if c not in seen or seen[c][0] == "running" or seen[c][-1] != "running"]
if unshown:
    sys.exit(f"the progress did not show these containers waiting or starting, then running: {unshown}")
if carried:
    sys.exit("an answer of the wizard carried AIO's passphrase")

config = json.loads(aio("configuration.json"))
reported = {key: config.get(key) for key in SWITCHES}
backup = aio("daily_backup_time").splitlines()
print(f"AIO reports {reported}; daily backup {backup}")
if reported != SWITCHES or backup[:2] != [BACKUP_TIME, "automaticUpdatesAreNotEnabled"]:
    sys.exit(f"AIO does not report our components, every other one off, and nightly backups to {BACKUP_FOLDER}")

store = machine("sudo docker volume inspect --format '{{.Mountpoint}}' nextcloud_aio_mastercontainer").strip()
machine(f"sudo test -s {store}/data/configuration.json && ! test -r {store}/data/configuration.json")
print(f"«Listo» shows https://{name}; AIO keeps its passphrase in {store}/data/configuration.json, which only root "
      "reads, and no answer of the wizard carried it")
