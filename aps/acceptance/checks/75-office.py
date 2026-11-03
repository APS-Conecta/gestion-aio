#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Office as in vanilla Nextcloud: a staff member signed in at the suite's name opens the Office entry and creates a
blank DOCX there, which opens in Euro-Office; they type a line and save, and the DOCX Nextcloud keeps holds it.
Reopened from the Office entry, Euro-Office shows that line, and a second one saves too. The staff account is made
here with occ, its password on stdin only and masked in the log."""

import base64
import contextlib
import io
import json
import os
import secrets
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser import chromium, machine  # noqa: E402
from playwright.sync_api import Error  # noqa: E402

USER, FILE = "prueba.oficina", "acta-de-prueba.docx"
NEXTCLOUD = "sudo docker exec --interactive --user www-data nextcloud-aio-nextcloud"
# In every frame, what the Euro-Office editor it holds reports: its document loaded, and whether it holds an edit the
# document server does not have yet.
EDITOR_STATE = """window.addEventListener('message', event => {
  if (![...document.querySelectorAll('iframe[name="frameEditor"]')].some(f => f.contentWindow === event.source)) return;
  let message;
  try { message = typeof event.data === 'string' ? JSON.parse(event.data) : event.data; } catch (error) { return; }
  if (message.event === 'onDocumentReady') window.documentReady = true;
  if (message.event === 'onDocumentStateChange') {
    window.unsaved = message.data;
    window.edited = window.edited || message.data;
  }
});"""

evidence = Path(os.environ["EVIDENCE"])
name = json.loads(machine("sudo docker exec nextcloud-aio-mastercontainer cat "
                          "/mnt/docker-aio-config/data/configuration.json")).get("domain")
if not name:
    sys.exit("the suite has no name: «Dirección» claimed none")
suite = f"https://{name}"
password = secrets.token_urlsafe(18)
print(f"::add-mask::{password}", flush=True)
machine(f"{NEXTCLOUD} sh -c 'read -r OC_PASS && export OC_PASS && "
        f"php occ user:add --password-from-env --display-name \"Prueba Oficina\" {USER}'", stdin=f"{password}\n")


def until(read, wait, failure):
    deadline = time.monotonic() + wait
    while not (answer := read()):
        if time.monotonic() > deadline:
            sys.exit(failure)
        time.sleep(1)
    return answer


def editor(context, state="documentReady"):
    """The page and frame of the Euro-Office editor served under the suite's name, once `state` holds in the frame
    that holds the editor."""
    def found():
        for page in context.pages:
            for frame in page.frames:
                if not (frame.url.startswith(f"{suite}/eurooffice/") and "/documenteditor/" in frame.url):
                    continue
                with contextlib.suppress(Error):  # a frame that navigates away
                    if frame.parent_frame.evaluate(state) and frame.locator("#editor_sdk").is_visible():
                        return page, frame
        return None
    return until(found, 120, f"Euro-Office under {suite}/eurooffice/ did not reach {state} within 2 minutes")


def edit(context, line):
    """Type the line at the document's end and save; Euro-Office stores the document once its editor closes."""
    page, frame = editor(context)
    frame.locator("#editor_sdk").click(position={"x": 300, "y": 200})
    frame.locator("#area_id").focus()
    for key in ("Control+End", "Enter"):
        page.keyboard.press(key)
    page.keyboard.type(line, delay=30)
    page.keyboard.press("Control+s")
    editor(context, "window.edited === true && window.unsaved === false")
    for opened in context.pages:
        opened.goto("about:blank")


def kept():
    """The text of the DOCX Nextcloud keeps."""
    docx = base64.b64decode(machine(f"{NEXTCLOUD} base64 /mnt/ncdata/{USER}/files/{FILE}"))
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        return "".join(ET.fromstring(archive.read("word/document.xml")).itertext())


def saved(*lines):
    until(lambda: all(line in kept() for line in lines), 120, f"Nextcloud does not keep {lines} in {FILE}")
    print(f"{FILE} keeps {', '.join(lines)}")


def documents(page):
    """The Office entry's documents."""
    page.get_by_text("Documentos", exact=True).first.click()


first, second = (f"APS-{n}-{secrets.token_hex(4)}" for n in ("PRIMERA", "SEGUNDA"))
with chromium() as browser:
    context = browser.new_context(locale="es-CL", viewport={"width": 1600, "height": 1000})
    context.add_init_script(EDITOR_STATE)
    page = context.new_page()
    try:
        page.goto(f"{suite}/login")
        page.fill("#user", USER)
        page.fill("#password", password)
        page.click("[data-login-form-submit]")
        page.wait_for_url(lambda url: "/login" not in url, timeout=60_000)
        # Nextcloud's welcome slides, on a first login while the suite still runs them, marked seen as their own close
        # button marks them.
        page.evaluate("fetch('/index.php/apps/firstrunwizard/wizard', {method: 'DELETE', "
                      "headers: {requesttoken: document.head.dataset.requesttoken}}).then(response => response.status)")
        page.reload()

        page.locator('#header a[href*="/apps/office"]:visible').first.click()
        documents(page)
        page.get_by_role("button", name="Vacío", exact=True).first.click()
        page.get_by_role("dialog").get_by_role("textbox").fill(FILE)
        with page.expect_response(lambda response: "/apps/files/api/v1/templates/create" in response.url) as created:
            page.get_by_role("button", name="Crear", exact=True).click()
        if not created.value.ok:
            sys.exit(f"the Office entry did not create {FILE}: HTTP {created.value.status}")
        editor(context)[0].screenshot(path=evidence / "creado.png")
        edit(context, first)
        saved(first)

        page = context.pages[0]
        page.goto(f"{suite}/apps/office/")
        documents(page)
        page.get_by_text(FILE, exact=True).first.click()
        page, frame = editor(context)
        frame.locator("#editor_sdk").click(position={"x": 300, "y": 200})
        page.keyboard.press("Control+f")
        search = frame.locator("#search-bar-text")
        search.fill(first)
        search.press("Enter")
        until(lambda: frame.locator("#search-bar-results").inner_text().rsplit("/", 1)[-1].strip() not in ("", "0"),
              30, f"the reopened document does not show {first}")
        page.screenshot(path=evidence / "reabierto.png")
        search.press("Escape")
        edit(context, second)
        saved(first, second)
    except BaseException:
        for n, opened in enumerate(context.pages):
            with contextlib.suppress(Error):
                opened.screenshot(path=evidence / f"fallo-{n}.png")
        raise
print(f"{USER} created {FILE} from the Office entry, edited it in Euro-Office, reopened it showing the saved line and "
      "saved a second one")
