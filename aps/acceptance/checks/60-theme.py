#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every page staff see wears the APS Conecta theme and says «APS Conecta Gestión». The login page and the dashboard
load the theme's stylesheet, theming's colours are the brand token file's, Nunito Sans is the font Nextcloud serves,
and the login page names the establishment. A person who logs in sees no «Nextcloud» and no link to nextcloud.com on
the login page, the dashboard, Files, Talk or the personal settings, which link the suite's source code and its
licence. The custom menu's stylesheet paints it in the token file's dark violet. The theme survives a restart of
Nextcloud's container, and the support app stays off."""

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
import brand  # noqa: E402
from browser import chromium, machine, person, suite_name  # noqa: E402
from playwright.sync_api import Error  # noqa: E402
from steps.theme import THEME  # noqa: E402

PERSON = "tema-prueba"
SOURCE = "https://github.com/APS-Conecta/gestion-aio"
PAGES = {"dashboard": ("/apps/dashboard/", "#app-dashboard"), "files": ("/apps/files/", "#app-navigation-vue"),
         "talk": ("/apps/spreed/", "#app-navigation-vue"), "settings": ("/settings/user", f'a[href="{SOURCE}"]')}
# What the page shows of the theme: its stylesheet, theming's colours, the fonts it loaded and the establishment's name.
THEMED = """async () => {
  await document.fonts.ready;
  const style = getComputedStyle(document.body), headline = document.querySelector('.login-form__headline');
  return {
    stylesheet: [...document.styleSheets].some(s => (s.href || '').includes('/themes/%s/core/css/server.css')),
    primary: style.getPropertyValue('--color-primary').trim().toLowerCase(),
    backdrop: style.getPropertyValue('--color-background-plain').trim().toLowerCase(),
    fonts: [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family.replaceAll('"', '')),
    establishment: headline ? getComputedStyle(headline, '::before').content : null,
  };
}""" % THEME

tokens = brand.tokens()
evidence = Path(os.environ["EVIDENCE"])
suite = f"https://{suite_name()}"
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
    if "Nunito Sans" not in found["fonts"]:
        wrong.append(f"{label} did not load Nunito Sans")
    if label.startswith("login") and found["establishment"] in (None, "none", "normal", '""'):
        wrong.append(f"{label} does not name the establishment")
    return wrong


def login_page(page, label):
    page.goto(f"{suite}/login")
    page.wait_for_selector("#user")
    return themed(page, label) + shows(page, label)


problems = []
with chromium() as browser:
    page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="es-CL")
    problems += login_page(page, "login")
    page.fill("#user", PERSON)
    page.fill("#password", password)
    page.click("button[type=submit]")
    page.wait_for_url(lambda url: "/login" not in url, timeout=60_000)
    for label, (path, ready) in PAGES.items():
        page.goto(f"{suite}{path}")
        page.wait_for_selector(ready, timeout=60_000)
        problems += (themed(page, label) if label == "dashboard" else []) + shows(page, label)
    if not page.locator(f'a[href="{SOURCE}"]').is_visible():
        problems.append("the personal settings do not link the source code")
    if not page.locator('a[href="https://www.gnu.org/licenses/agpl-3.0.html"]').is_visible():
        problems.append("the personal settings do not link the licence")
    menu = page.context.request.get(f"{suite}/apps/side_menu/css/stylesheet").text()
    if f"--side-menu-background-color: {tokens['--aps-violet-dark']}" not in menu:
        problems.append("the custom menu's stylesheet does not paint it in the token file's dark violet")

    machine("sudo docker restart nextcloud-aio-nextcloud")
    deadline = time.monotonic() + 10 * 60
    while True:
        try:
            status = page.context.request.get(f"{suite}/status.php").json()
            if status.get("installed") and not status.get("maintenance"):
                break
        except (Error, ValueError):
            pass
        if time.monotonic() > deadline:
            sys.exit("Nextcloud did not answer within 10 minutes of its container's restart")
        time.sleep(10)
    page.context.clear_cookies()
    problems += login_page(page, "login-after-restart")
    support = machine("sudo docker exec --user www-data nextcloud-aio-nextcloud php occ config:app:get support enabled")
    print(f"support app after the restart: {support.strip()}")
    if support.strip() != "no":
        problems.append("the support app is on again after Nextcloud's restart")

if problems:
    sys.exit("\n".join(problems))
print("the theme is on every page, with no «Nextcloud», and survives a restart of Nextcloud's container")
