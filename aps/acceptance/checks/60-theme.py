#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every page staff see wears the APS Conecta theme and says «APS Conecta Gestión». The login page and the dashboard
load the theme's stylesheet, theming's colours are the brand token file's, Nunito Sans is the font Nextcloud serves,
and the login page names the establishment and shows the suite's motto after its name, with no console error and no
content-security-policy violation. Its «¿Olvidó su contraseña?» link opens the help page, which the server answers from
the theme folder with 200 and which names the establishment in the brand token file's fonts, with no console error and
no content-security-policy violation, and links back to the login page; Nextcloud's email reset is refused, so no screen
says a message was sent. A person who logs in sees no «Nextcloud» and no link to nextcloud.com on
the login page, the dashboard, Files, Talk or the personal settings, which link the suite's source code and its
licence. The custom menu's stylesheet paints it in the token file's dark violet. The theme survives a restart of
Nextcloud's container, and the support app stays off. Its start rebuilds the browser's Spanish of the apps the theme
overrides texts of, after upstream's start updated them: Talk's, left stale before the restart, is its own Spanish as
Nextcloud holds it with the theme's overrides on top after it, and Talk names no «Nextcloud» in a fresh browser."""

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
import brand  # noqa: E402
from browser import (NEXTCLOUD, chromium, faults, fonts, log_in, machine, occ, person,  # noqa: E402
                     provisionador_state, suite_name)
from playwright.sync_api import Error  # noqa: E402
from steps.theme import HELP, PLACED, SUITE, THEME  # noqa: E402

PERSON = "tema-prueba"
WINDOW = {"viewport": {"width": 1280, "height": 900}, "locale": "es-CL"}
SOURCE = "https://github.com/APS-Conecta/gestion-aio"
PAGES = {"dashboard": ("/apps/dashboard/", "#app-dashboard"), "files": ("/apps/files/", "#app-navigation-vue"),
         "talk": ("/apps/spreed/", "#app-navigation-vue"), "settings": ("/settings/user", f'a[href="{SOURCE}"]')}
# What the page shows of the theme: its stylesheet, theming's colours and the establishment's name.
THEMED = """() => {
  const style = getComputedStyle(document.body), headline = document.querySelector('.login-form__headline');
  return {
    stylesheet: [...document.styleSheets].some(s => (s.href || '').includes('/themes/%s/core/css/server.css')),
    primary: style.getPropertyValue('--color-primary').trim().toLowerCase(),
    backdrop: style.getPropertyValue('--color-background-plain').trim().toLowerCase(),
    establishment: headline ? getComputedStyle(headline, '::before').content : null,
  };
}""" % THEME
# In-page requests, so they go through Chromium's own TLS (which trusts the suite's roots) and the page's session.
TEXT = "url => fetch(url, {cache: 'no-store'}).then(r => r.text())"
STATUS = "url => fetch(url, {cache: 'no-store'}).then(r => r.ok ? r.json() : {})"
# Nextcloud's email reset, posted as its own reset form posts it from the login page, with the page's request token.
RESET = """user => fetch('/lostpassword/email', {method: 'POST', body: JSON.stringify({user}),
  headers: {'Content-Type': 'application/json', requesttoken: document.head.dataset.requesttoken}})
  .then(r => r.text().then(text => ({status: r.status, text})))"""
TALK = "/var/www/html/custom_apps/spreed/l10n/es"  # Talk's own Spanish, .json and .js
BROWSER = f"{PLACED}/core/spreed/l10n/es.js"  # the theme's browser file for Talk, which the browser loads in its place

tokens = brand.tokens()
evidence = Path(os.environ["EVIDENCE"])
suite = f"https://{suite_name()}"
help_link = f"{suite}/themes/{THEME}/{HELP}"
establishment = provisionador_state()["centre"]["short_name"]
password = person(PERSON, "Persona de prueba")


def shows(page, label):
    """The page shows no «Nextcloud» and links no nextcloud.com; its screenshot goes to the evidence."""
    page.wait_for_timeout(3000)  # the app's own requests fill it in
    page.screenshot(path=evidence / f"{label}.png", full_page=True)
    text = page.title() + "\n" + page.inner_text("body")
    links = page.eval_on_selector_all("a[href*='nextcloud.com']",
                                      "links => links.filter(a => a.offsetParent).map(a => a.href)")
    named = [line.strip() for line in text.splitlines() if "Nextcloud" in line]
    print(f"{label}: title {page.title()!r}, lines naming Nextcloud {named}, links to nextcloud.com {links}")
    return ([f"{label} shows «Nextcloud»: {named}"] if named else []) + (
        [f"{label} links nextcloud.com: {links}"] if links else [])


def themed(page, label):
    """The page wears the theme in the brand token file's colours and fonts."""
    found = page.evaluate(THEMED)
    print(f"{label}: {found}")
    wrong = [] if found["stylesheet"] else [f"{label} does not load the theme's stylesheet"]
    if (found["primary"], found["backdrop"]) != (tokens["--aps-violet"], tokens["--aps-violet-dark"]):
        wrong.append(f"{label}'s colours are not the brand token file's")
    if "Nunito Sans" not in fonts(page):
        wrong.append(f"{label} did not load Nunito Sans")
    if label.startswith("login") and found["establishment"] in (None, "none", "normal", '""'):
        wrong.append(f"{label} does not name the establishment")
    return wrong


def says_the_motto(page, label):
    """The page shows the motto after the suite's name, as Nextcloud's footer shows theming's slogan."""
    texts = [shown.inner_text() for shown in page.get_by_text(brand.MOTTO).all() if shown.is_visible()]
    print(f"{label}: the motto shows in {texts}")
    if any(brand.MOTTO in text.partition(SUITE)[2] for text in texts):
        return []
    return [f"{label} does not show the motto after «{SUITE}»"]


def login_page(page, label):
    found = faults(page)
    page.goto(f"{suite}/login")
    page.wait_for_selector("#user")
    wrong = themed(page, label) + says_the_motto(page, label) + shows(page, label)
    return wrong + ([f"{label} logged errors or content-security-policy violations: {found}"] if found else [])


def email_reset_refused(page):
    """Nextcloud's email reset answers, in its own Spanish, that reset is disabled: it sends nothing."""
    answer = page.evaluate(RESET, PERSON)
    print(f"Nextcloud's email reset answers {answer['status']}: {answer['text'][:200]}")
    core = json.loads(machine(f"{NEXTCLOUD} cat /var/www/html/core/l10n/es.json"))["translations"]
    disabled = core.get("Password reset is disabled", "Password reset is disabled")
    try:
        said = json.loads(answer["text"])
    except ValueError:
        said = {}
    if answer["status"] == 200 and said == {"status": "error", "msg": disabled}:
        return []
    return [f"Nextcloud's email reset does not answer «{disabled}»"]


def help_page(page):
    """From the login page, «¿Olvidó su contraseña?» opens the help page: 200 from the server, the establishment's name,
    the brand token file's fonts, no console error nor content-security-policy violation, and a link back to the login
    page that works."""
    found = faults(page)
    forgot = page.locator(f'a[href="{help_link}"]')
    if not forgot.is_visible():
        return [f"the login page does not link {help_link}"]
    print(f"login: «{forgot.inner_text().strip()}» links {help_link}")
    wrong = []
    with page.expect_response(help_link) as answer:
        forgot.click()
    page.wait_for_load_state()
    page.screenshot(path=evidence / "help.png", full_page=True)
    loaded = fonts(page)
    print(f"help page: {answer.value.status}, fonts {loaded}")
    if answer.value.status != 200:
        wrong.append(f"the server answers the help page with {answer.value.status}")
    if establishment not in page.inner_text("body"):
        wrong.append(f"the help page does not name {establishment}")
    if not {"Fraunces", "Nunito Sans"} <= set(loaded):
        wrong.append("the help page did not load the brand token file's fonts")
    page.get_by_role("link", name="Volver al inicio de sesión").click()
    try:
        page.wait_for_selector("#user", timeout=30_000)
    except Error:
        wrong.append("the help page's link does not lead back to the login page")
    return wrong + ([f"the help page logged errors or content-security-policy violations: {found}"] if found else [])


def open_page(page, label):
    path, ready = PAGES[label]
    page.goto(f"{suite}{path}")
    page.wait_for_selector(ready, timeout=60_000)


def stale_talk_texts():
    """Where the theme's browser file for Talk is not Talk's own Spanish, as Nextcloud holds it now, with the theme's
    overrides on top: the texts that differ, or a wrong app id or plural form."""
    own = json.loads(machine(f"{NEXTCLOUD} cat {TALK}.json"))
    overrides = json.loads(machine(f"{NEXTCLOUD} cat {PLACED}/custom_apps/spreed/l10n/es.json"))["translations"]
    script = machine(f"{NEXTCLOUD} cat {BROWSER}").strip().removeprefix("OC.L10N.register(").removesuffix(");")
    app, texts, plural = json.loads(f"[{script}]")
    expected = {**own["translations"], **overrides}
    differ = sorted(key for key in expected.keys() | texts.keys() if texts.get(key) != expected.get(key))
    stale = [f"Talk's browser file registers the app {app!r}, not 'spreed'"] if app != "spreed" else []
    if plural != own["pluralForm"]:
        stale.append(f"Talk's browser file has the plural form {plural!r}, not Talk's {own['pluralForm']!r}")
    if differ:
        stale.append(f"Talk's browser file is not its own Spanish with the theme's overrides on top for: {differ}")
    print("Talk's browser texts after the restart:", "; ".join(stale) or "its own Spanish with the overrides on top")
    return stale


problems = []
with chromium() as browser:
    page = browser.new_page(**WINDOW)
    problems += login_page(page, "login") + email_reset_refused(page) + help_page(page)
    log_in(page, suite, PERSON, password)
    for label in PAGES:
        open_page(page, label)
        problems += (themed(page, label) if label == "dashboard" else []) + shows(page, label)
    if not page.locator(f'a[href="{SOURCE}"]').is_visible():
        problems.append("the personal settings do not link the source code")
    if not page.locator('a[href="https://www.gnu.org/licenses/agpl-3.0.html"]').is_visible():
        problems.append("the personal settings do not link the licence")
    menu = page.evaluate(TEXT, f"{suite}/apps/side_menu/css/stylesheet")
    if f"--side-menu-background-color: {tokens['--aps-violet-dark']}" not in menu:
        problems.append("the custom menu's stylesheet does not paint it in the token file's dark violet")

    machine(f"{NEXTCLOUD} cp {TALK}.js {BROWSER}")  # a stale browser file for Talk: its own, naming «Nextcloud»
    page.goto(f"{suite}/status.php")  # a page with no script of its own to reload it while Nextcloud restarts
    machine("sudo docker restart nextcloud-aio-nextcloud")
    deadline = time.monotonic() + 10 * 60
    while True:
        try:
            status = page.evaluate(STATUS, f"{suite}/status.php")
            if status.get("installed") and not status.get("maintenance"):
                break
        except Error:
            pass
        if time.monotonic() > deadline:
            sys.exit("Nextcloud did not answer within 10 minutes of its container's restart")
        time.sleep(10)
    page = browser.new_page(**WINDOW)  # a new context: no cookie and no cached file
    problems += login_page(page, "login-after-restart")
    log_in(page, suite, PERSON, password)
    open_page(page, "talk")
    problems += shows(page, "talk-after-restart") + stale_talk_texts()
    support = occ("config:app:get support enabled")
    print(f"support app after the restart: {support.strip()}")
    if support.strip() != "no":
        problems.append("the support app is on again after Nextcloud's restart")

if problems:
    sys.exit("\n".join(problems))
print("the theme is on every page, with no «Nextcloud», and survives a restart of Nextcloud's container, which "
      "rebuilds Talk's browser texts")
