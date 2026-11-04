#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Office as in vanilla Nextcloud: a staff member signed in at the suite's name opens the Office entry from the custom
menu and creates a blank DOCX there, which opens in Euro-Office; they type a line and save, and the DOCX Nextcloud keeps
holds it. Reopened from the Office entry, Euro-Office shows that line, and a second one saves too."""

import base64
import contextlib
import io
import json
import os
import re
import secrets
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser import NEXTCLOUD, chromium, machine, person, suite_name  # noqa: E402
from playwright.sync_api import Error  # noqa: E402

USER, FILE = "prueba.oficina", "acta-de-prueba.docx"
# In every frame, each message it receives that names an event, as Euro-Office's Gateway.js posts DocsAPI's events
# (onDocumentReady, onDocumentStateChange, ...) to the frame holding its iframe "frameEditor": the event, its data, the
# second since the frame loaded, and whether that iframe sent it.
EDITOR_EVENTS = """window.editorEvents = [];
window.addEventListener('message', event => {
  let message;
  try { message = typeof event.data === 'string' ? JSON.parse(event.data) : event.data; } catch (error) { return; }
  const name = message?.event ?? message?.method;
  if (typeof name !== 'string') return;
  const editors = [...document.querySelectorAll('iframe[name="frameEditor"]')];
  const editor = editors.some(f => f.contentWindow === event.source);
  window.editorEvents.push([name, message.data ?? null, Math.round(performance.now() / 1000), editor]);
});"""
# What the editor reported: its document loaded; it held an edit, and then the document server had every edit.
READY = "its document ready", "editorEvents.some(([name, , , editor]) => editor && name === 'onDocumentReady')"
SAVED = "the edit saved", """(() => {
  const states = editorEvents.filter(([name, , , editor]) => editor && name === 'onDocumentStateChange');
  return states.some(([, unsaved]) => unsaved) && states.at(-1)[1] === false;
})()"""

evidence = Path(os.environ["EVIDENCE"])
suite = f"https://{suite_name()}"
password = person(USER, "Prueba Oficina")


def until(read, wait, failure):
    deadline = time.monotonic() + wait
    while not (answer := read()):
        if time.monotonic() > deadline:
            sys.exit(failure() if callable(failure) else failure)
        time.sleep(1)
    return answer


def served(frame):
    """Whether the frame is Euro-Office's document editor, served under the suite's name."""
    return frame.url.startswith(f"{suite}/eurooffice/") and "/documenteditor/" in frame.url


def editor(context, state=READY):
    """The page and frame of the Euro-Office editor, once it reported `state` to the frame that holds it."""
    reported, holds = state

    def found():
        for page in context.pages:
            for frame in filter(served, page.frames):
                with contextlib.suppress(Error):  # a frame that navigates away
                    if frame.parent_frame.evaluate(holds) and frame.locator("#editor_sdk").is_visible():
                        return page, frame
        return None
    return until(found, 120, lambda: f"Euro-Office under {suite}/eurooffice/ did not report {reported} within 2 "
                                     f"minutes. The frames, nested, and the events each received:\n{seen(context)}")


def seen(context):
    """Each page's frames by URL without its query, nested, with the events each received (data that is no link or
    token) and, for the editor, whether its document shows."""
    lines = []
    for n, page in enumerate(context.pages):
        for frame in page.frames:
            depth, parent = 0, frame.parent_frame
            while parent:
                depth, parent = depth + 1, parent.parent_frame
            events, shows = None, ""
            with contextlib.suppress(Error):
                events = frame.evaluate("window.editorEvents")
                if served(frame):
                    shows = f" [#editor_sdk visible: {frame.locator('#editor_sdk').is_visible()}]"
            received = "no listener" if events is None else "; ".join(
                f"{name}{'' if data in (None, {}) else ' ' + json.dumps(shown(data))} at {second} s"
                f"{'' if editor else ' (not from frameEditor)'}" for name, data, second, editor in events) or "none"
            lines.append(f"{'  ' * depth}page {n} {frame.url.split('?')[0]}{shows}: {received}")
    return "\n".join(lines)


def shown(data):
    if not isinstance(data, dict):
        return data
    return {k: v for k, v in data.items() if not (isinstance(v, (dict, list)) or re.search("url|token|link", k, re.I))}


def edit(context, line):
    """Type the line at the document's end and save; Euro-Office stores the document once its editor closes."""
    page, frame = editor(context)
    for tip in frame.get_by_text("Entendido", exact=True).all():  # the editor's «Nuevo» tips, closed as a person does
        if tip.is_visible():
            tip.click()
    frame.locator("#editor_sdk").click(position={"x": 300, "y": 200})
    frame.locator("#area_id").focus()
    for key in ("Control+End", "Enter"):
        page.keyboard.press(key)
    page.keyboard.type(line, delay=30)
    page.keyboard.press("Control+s")
    editor(context, SAVED)
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


def office(page):
    """The Office entry's documents, opened from the custom menu as staff open it."""
    page.locator("#header .cm-opener").click()
    page.locator('#side-menu a[href*="/apps/office"]').click()
    page.get_by_text("Documentos", exact=True).first.click()


first, second = (f"APS-{n}-{secrets.token_hex(4)}" for n in ("PRIMERA", "SEGUNDA"))
with chromium() as browser:
    context = browser.new_context(locale="es-CL", viewport={"width": 1600, "height": 1000})
    context.add_init_script(EDITOR_EVENTS)
    page = context.new_page()
    try:
        page.goto(f"{suite}/login")
        page.fill("#user", USER)
        page.fill("#password", password)
        page.click("[data-login-form-submit]")
        page.wait_for_url(lambda url: "/login" not in url, timeout=60_000)
        office(page)
        page.get_by_role("button", name="Vacío", exact=True).first.click()
        page.get_by_role("dialog").get_by_role("textbox").fill(FILE)
        with page.expect_response(lambda response: "/apps/files/api/v1/templates/create" in response.url) as created:
            page.get_by_role("button", name="Crear", exact=True).click()
        if not created.value.ok:
            sys.exit(f"the Office entry did not create {FILE}: HTTP {created.value.status}")
        editor(context)[0].screenshot(path=evidence / "created.png")
        edit(context, first)
        saved(first)

        page = context.pages[0]
        page.goto(suite)
        office(page)
        page.get_by_text(FILE, exact=True).first.click()
        page, frame = editor(context)
        frame.locator("#editor_sdk").click(position={"x": 300, "y": 200})
        page.keyboard.press("Control+f")
        search = frame.locator("#search-bar-text")
        search.fill(first)
        search.press("Enter")
        until(lambda: frame.locator("#search-bar-results").inner_text().rsplit("/", 1)[-1].strip() not in ("", "0"),
              30, f"the reopened document does not show {first}")
        page.screenshot(path=evidence / "reopened.png")
        search.press("Escape")
        edit(context, second)
        saved(first, second)
    except BaseException:
        for n, opened in enumerate(context.pages):
            with contextlib.suppress(Error):
                opened.screenshot(path=evidence / f"failed-{n}.png")
        raise
print(f"{USER} created {FILE} from the Office entry, edited it in Euro-Office, reopened it showing the saved line and "
      "saved a second one")
