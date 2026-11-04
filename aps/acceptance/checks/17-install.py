#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""«Revisar» and «Instalar», from a fresh link as an operator who comes back to the wizard: «Dirección» goes on to
«Revisar», which summarises the centre's address, the components and the backups; «Instalar» shows each step and, once
AIO runs our components, each container's progress live until «Listo» shows the suite's address. There the wizard hands
over the passwords file, the same as the root-only copy on the machine, with the administrator's first password and
AIO's passphrase, and closes; the page then still tells, behind a resolver that drops the name's answer, the router
change, and otherwise that this computer opens the suite (no check goes back to «Dirección» after «Instalar»). AIO
reports our components on, every other optional one off and nightly backups to the backup folder. AIO's passphrase
reaches the browser only in the passwords file, and on the machine only root reads the file AIO keeps it in."""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
from browser import aio_config, aio_file, chromium, machine, suite_name, verified, wizard_link  # noqa: E402
from playwright.sync_api import expect  # noqa: E402
from steps.backups import BACKUP_FOLDER, BACKUP_TIME  # noqa: E402
from steps.components import COMPONENTS  # noqa: E402
from steps.passwords import FILE  # noqa: E402

CHOSEN = list(COMPONENTS.values())  # «Revisar» lists them, and the progress names their containers so
SWITCHES = {"isTalkEnabled": True, "officeSuite": "eurooffice", "isImaginaryEnabled": True, "isClamavEnabled": True,
            "isFulltextsearchEnabled": True, "isHarpEnabled": True, "isTalkRecordingEnabled": False,
            "isDockerSocketProxyEnabled": False, "isWhiteboardEnabled": False, "aio_community_containers": "caddy",
            "borg_backup_host_location": BACKUP_FOLDER, "borg_remote_repo": ""}
KEPT = f"/var/lib/aps-conecta/provisionador/{FILE}"  # the passwords file's root-only copy


passphrase, name = aio_config().get("password"), suite_name()
if not passphrase:  # it stays in memory: run.sh masks it in the log
    sys.exit("AIO stored no passphrase")
suite = f"https://{name}"
router = f"En el router, permita esas respuestas para {name.split('.', 1)[1]}"
dropped = f"{suite}/**"  # what the page fetches to tell whether this computer opens the suite
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
    expect(page.locator("#summary-components li")).to_have_text(CHOSEN)
    expect(page.locator("#summary-backups")).to_contain_text(BACKUP_FOLDER)
    page.screenshot(path=evidence / "revisar.png")

    page.route(dropped, lambda route: route.abort("namenotresolved"))  # a resolver that drops the name's answer
    page.click("#install")
    seen = {}  # each container's states, in the order the page showed them
    deadline = time.monotonic() + 12 * 60
    while not page.locator("#listo").is_visible():
        for container, state in page.eval_on_selector_all(
                "#progress li", "items => items.map(i => [i.querySelector('strong').textContent, i.dataset.state])"):
            if seen.setdefault(container, [state])[-1] != state:
                seen[container].append(state)
        if page.locator("#install").is_visible() or time.monotonic() > deadline:
            page.screenshot(path=evidence / "revisar-failed.png")
            sys.exit(f"no «Listo» within 12 minutes: {page.inner_text('#install-status')}\n{page.inner_text('#steps')}")
        if {"running", "stopped"} <= {s[-1] for s in seen.values()} and not (evidence / "progress.png").exists():
            page.screenshot(path=evidence / "progress.png")
        page.wait_for_timeout(2000)
    progress = "".join(f"{c}: {' → '.join(s)}\n" for c, s in seen.items())
    (evidence / "progress.txt").write_text(progress, encoding="utf-8")
    print(progress, end="")
    expect(page.locator("#suite-address")).to_have_text(suite)
    expect(page.locator("#suite-address")).to_have_attribute("href", suite)
    expect(page.locator("#closed")).to_contain_text("se cerró y su enlace ya no funciona")
    with page.expect_download() as download:
        page.click("#passwords")
    handed = Path(download.value.path()).read_text(encoding="utf-8")
    if page.evaluate("fetch('/api/state').then(r => r.status, () => 'closed')") not in ("closed", 401):
        sys.exit("the wizard's session still works after «Listo»")
    expect(page.locator("#reach")).to_contain_text(router, timeout=60_000)
    page.screenshot(path=evidence / "router.png")
    print(f"router: {page.inner_text('#reach')}")
    page.unroute(dropped)
    expect(page.locator("#reach")).to_have_text(f"Este equipo abre {suite} correctamente.", timeout=180_000)
    page.screenshot(path=evidence / "listo.png")
    print(f"reach: {page.inner_text('#reach')}")
    context.close()
    answers = [(e["request"]["url"], e["response"]["content"].get("text", ""))
               for e in json.loads(har.read_text(encoding="utf-8"))["log"]["entries"]]

unshown = [c for c in CHOSEN if c not in seen or seen[c][0] == "running" or seen[c][-1] != "running"]
if unshown:
    sys.exit(f"the progress did not show these containers waiting or starting, then running: {unshown}")

config = aio_config()
administrator = config["secrets"]["NEXTCLOUD_PASSWORD"]  # never printed; run.sh masks it once AIO made it
carried = [url for url, text in answers
           if (passphrase in text or administrator in text) and not url.endswith("/api/close")]
if carried:
    sys.exit(f"these answers of the wizard carried AIO's passphrase or the administrator's password: {carried}")
kept = machine(f"sudo cat {KEPT}")
if not (handed == kept and passphrase in kept and administrator in kept):
    sys.exit("the passwords file «Listo» handed over is not the machine's copy with the administrator's first password "
             "and AIO's passphrase")
reported = {key: config.get(key) for key in SWITCHES}
backup = aio_file("daily_backup_time").splitlines()
print(f"AIO reports {reported}; daily backup {backup}")
if reported != SWITCHES or backup[:2] != [BACKUP_TIME, "automaticUpdatesAreNotEnabled"]:
    sys.exit(f"AIO does not report our components, every other one off, and nightly backups to {BACKUP_FOLDER}")

store = machine("sudo docker volume inspect --format '{{.Mountpoint}}' nextcloud_aio_mastercontainer").strip()
machine(f"sudo test -s {store}/data/configuration.json && ! test -r {store}/data/configuration.json")
print(f"«Listo» shows {suite} and hands over the passwords file; AIO keeps its passphrase in "
      f"{store}/data/configuration.json, which only root reads, and only the passwords file carried it to the browser")
