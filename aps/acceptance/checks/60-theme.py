#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every page staff see wears the APS Conecta theme and says «APS Conecta Gestión». The login page, «Inicio», Files,
Talk and the personal settings load the theme's stylesheet; on each, every Nextcloud variable of the variable map holds
its brand role's value from the brand token file, the main background a solid colour, each text colour it sets reads at
4.5:1 or more on the background it sits on, and each element colour at 3:1 or more on the main background; Nunito Sans
is the font Nextcloud serves, at Nextcloud's own size and line height, and the first visible heading is in the display
font with the brand manual's letter spacing, with no italic face of either. The logged-in top bar shows the gradient
from dark violet to violet, and the login page's top area none. The login page names the establishment and shows the
suite's motto after its name, with no console error and no content-security-policy violation. Its «¿Contraseña
olvidada?» link opens the help page, which the server answers from the theme folder with 200 and which names the
establishment in the brand token file's fonts, with no console error and no content-security-policy violation, and links
back to the login page; Nextcloud's email reset is refused, so no screen says a message was sent. Tabbing to the login
form gives the focused control the manual's ring: a 3 px outline in the primary element colour, wider when the system
asks for more contrast, which also gives secondary text the ink and borders the muted colour; when the system asks for
reduced motion, Nextcloud's animations take no time. In the top bar, the ring takes the backdrop's text colour, and in
its menus' panels the primary element colour again. A staff member who logs in lands on «Inicio», the welcome page, the
only home: Nextcloud's Dashboard is off, and its address does not open it. «Inicio» speaks IntraVox's own Spanish: its
edit button says «Editar página», never «Edit page». No staff member sees «Nextcloud» or a link to nextcloud.com on the
login page, «Inicio», Files, Talk or the personal settings, which link the suite's source code and its licence. From 601
px wide, the top bar names the establishment right of the logo; a narrower window shows the logo alone. The custom
menu's stylesheet paints it in the token file's dark violet. The theme ships its fonts as WOFF2 only. It survives a
restart of Nextcloud's container, and the support app stays off. Its start rebuilds the browser's Spanish of the apps
the theme overrides texts of, after upstream's start updated them: Talk's, left stale before the restart, is its own
Spanish as Nextcloud holds it with the theme's overrides on top after it, and Talk names no «Nextcloud» in a fresh
browser."""

import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
import brand  # noqa: E402
import teams  # noqa: E402
from browser import (NEXTCLOUD, chromium, faults, fonts, log_in, machine, occ, person,  # noqa: E402
                     provisionador_state, suite_name)
from playwright.sync_api import Error  # noqa: E402
from steps.theme import HELP, PLACED, SUITE, THEME  # noqa: E402
from steps.welcome import APP, DASHBOARD, GREETING  # noqa: E402

PERSON = "tema-prueba"
WINDOW = {"viewport": {"width": 1280, "height": 900}, "locale": "es-CL"}
NAMED_FROM = 601  # px: the narrowest window whose top bar names the establishment
NOTHING = (None, "none", "normal", '""')  # a pseudo-element's content when it shows nothing
SOURCE = "https://github.com/APS-Conecta/gestion-aio"
# Each page by its label: its address and what shows once it is ready. «Inicio» is ready once its seeded first heading
# shows, matched exactly: IntraVox's own Spanish has «bienvenida» in other texts.
PAGES = {"inicio": (f"/apps/{APP}/", f'#app-{APP} >> text="{GREETING}"'),
         "files": ("/apps/files/", "#app-navigation-vue"), "talk": ("/apps/spreed/", "#app-navigation-vue"),
         "settings": ("/settings/user", f'a[href="{SOURCE}"]')}
# The variable map: the brand role each Nextcloud 34 variable takes, which the theme sets.
VARIABLE_MAP = {
    "--color-primary-hover": "--aps-violet-hover", "--color-primary-element-hover": "--aps-violet-hover",
    "--color-primary-light": "--aps-violet-tint", "--color-primary-light-hover": "--aps-violet-tint-hover",
    "--color-primary-light-text": "--aps-violet-dark",
    "--color-main-text": "--aps-ink",
    "--color-text-maxcontrast": "--aps-muted", "--color-text-maxcontrast-default": "--aps-muted",
    "--color-background-hover": "--aps-canvas", "--color-background-dark": "--aps-canvas",
    "--color-background-darker": "--aps-border", "--color-border": "--aps-border",
    "--color-element-error": "--aps-error", "--color-element-warning": "--aps-warning",
    "--color-element-success": "--aps-success", "--color-element-info": "--aps-info",
    "--color-text-error": "--aps-error-text", "--color-error-text": "--aps-error-text",
    "--color-warning-text": "--aps-warning-text",
}
RADIUS = "6px"  # --border-radius, the manual's, which the map sets too
# The variables that already take their role without the map: from theming, from Nextcloud 34, or as Nextcloud's
# var() aliases of a mapped variable.
MEASURED = {
    "--color-primary": "--aps-violet", "--color-primary-element": "--aps-violet",
    "--color-primary-text": "--aps-paper", "--color-primary-element-text": "--aps-paper",
    "--color-main-background": "--aps-paper", "--color-background-plain": "--aps-violet-dark",
    "--color-background-plain-text": "--aps-paper",
    "--color-primary-element-light": "--aps-violet-tint",
    "--color-primary-element-light-hover": "--aps-violet-tint-hover",
    "--color-primary-element-light-text": "--aps-violet-dark",
}
# Text on the background it sits on needs 4.5:1, an element on the main background 3:1 (WCAG 2.2, 1.4.3 and 1.4.11).
CONTRAST = [(text, background, 4.5) for text, background in (
    ("--color-main-text", "--color-main-background"), ("--color-main-text", "--color-background-hover"),
    ("--color-text-maxcontrast", "--color-main-background"), ("--color-text-maxcontrast", "--color-background-hover"),
    ("--color-primary-element-text", "--color-primary-element-hover"),
    ("--color-primary-light-text", "--color-primary-light"),
    ("--color-primary-light-text", "--color-primary-light-hover"),
    ("--color-text-error", "--color-main-background"), ("--color-error-text", "--color-error"),
    ("--color-warning-text", "--color-warning"),
)] + [(element, "--color-main-background", 3) for element in (
    "--color-element-error", "--color-element-warning", "--color-element-success", "--color-element-info")]
KEPT = ("--color-error", "--color-warning")  # Nextcloud's pale status backgrounds: measured for the text on them only
# Nextcloud 34's type size and line height, which the manual keeps.
NEXTCLOUD_TYPE = {"--default-font-size": "15px", "--default-line-height": "1.5"}
LETTER_SPACING = -0.015  # em, the manual's for headings
RING = 3  # px, the manual's focus ring
# The variables a system preference changes, with the role or value each then takes.
MORE_CONTRAST = {"--color-text-maxcontrast": "--aps-ink", "--color-text-maxcontrast-default": "--aps-ink",
                 "--color-border": "--aps-muted"}
REDUCED_MOTION = {"--animation-quick": "0s", "--animation-slow": "0s"}
GRADIENT = ("--aps-violet-dark", "--aps-violet")  # the top bar's roles, from start to end
TABS = 40  # the most Tab presses it takes to reach a place on a page
FONT_FILES = (".ttf", ".otf", ".eot", ".woff", ".woff2")
# What the page shows of the theme: its stylesheet and the establishment's name.
THEMED = """() => {
  const headline = document.querySelector('.login-form__headline');
  return {
    stylesheet: [...document.styleSheets].some(s => (s.href || '').includes('/themes/%s/core/css/server.css')),
    establishment: headline ? getComputedStyle(headline, '::before').content : null,
  };
}""" % THEME
# Each variable's value on the body beside the value given for it, both as the browser computes a colour (rgb(…)), or
# as written when it is no colour.
VARIABLES = """expected => {
  const body = getComputedStyle(document.body), probe = document.body.appendChild(document.createElement('i'));
  const computed = value => {
    probe.style.color = '';
    probe.style.color = value;
    return probe.style.color ? getComputedStyle(probe).color : value;
  };
  const found = Object.fromEntries(Object.entries(expected).map(
    ([name, value]) => [name, [computed(body.getPropertyValue(name).trim()), computed(value)]]));
  probe.remove();
  return found;
}"""
# What the page shows of the brand's type and top bar: its first visible heading (the layout's hidden h1 when it shows
# none), with its font and letter spacing in em, the body's font, the brand fonts as the page computes them, the brand
# fonts' italic faces it declares, and the top bar's background image. Nextcloud hides a heading in a 1 px box.
TYPE_AND_TOP_BAR = """brand => {
  const probe = document.body.appendChild(document.createElement('i'));
  const family = value => (probe.style.fontFamily = value, getComputedStyle(probe).fontFamily);
  const first = [...document.querySelectorAll('h1, h2, h3')].find(h => h.checkVisibility() && h.offsetWidth > 1)
    || document.querySelector('h1');
  const heading = getComputedStyle(first), header = document.getElementById('header');
  const families = [brand.display, brand.text].map(fonts => fonts.split(',')[0].replaceAll('"', '').trim());
  const shown = {
    display: family(brand.display), text: family(brand.text), headingText: first.textContent.trim().slice(0, 60),
    heading: heading.fontFamily, spacing: parseFloat(heading.letterSpacing) / parseFloat(heading.fontSize) || null,
    body: getComputedStyle(document.body).fontFamily,
    italic: [...document.fonts].filter(face => families.includes(face.family.replaceAll('"', ''))
                                        && face.style !== 'normal').map(face => `${face.family} ${face.style}`),
    topBar: header ? getComputedStyle(header).backgroundImage : null,
  };
  probe.remove();
  return shown;
}"""
# The element with focus: what it is, whether it sits in `place` (a selector), and its outline.
FOCUSED = """place => {
  const focused = document.activeElement, outline = getComputedStyle(focused);
  return {name: focused.id || focused.getAttribute('aria-label') || focused.tagName.toLowerCase(),
          inside: focused !== document.body && !!focused.closest(place),
          style: outline.outlineStyle, width: parseFloat(outline.outlineWidth), colour: outline.outlineColor,
          offset: parseFloat(outline.outlineOffset)};
}"""
# What the top bar shows after the logo: the logo link's generated content, and the room the link gives it right of
# the logo, which sits in the link's start padding. A public share page's #nextcloud is no link.
TOP_BAR = """() => {
  const link = document.querySelector('a#nextcloud');
  if (!link) return {name: null, room: 0};
  const padding = getComputedStyle(link);
  return {name: getComputedStyle(link, '::before').content,
          room: link.clientWidth - parseFloat(padding.paddingInlineStart) - parseFloat(padding.paddingInlineEnd)};
}"""
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
core = json.loads(machine(f"{NEXTCLOUD} cat /var/www/html/core/l10n/es.json"))["translations"]
password = person(PERSON, "Persona de prueba")
occ(f"group:adduser {teams.STAFF} {PERSON}")  # staff, as the people step makes them: «Inicio» is theirs to edit


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


def luminance(colour):
    """A computed rgb(…) colour's relative luminance (WCAG 2.2)."""
    channels = [int(c) / 255 for c in re.findall(r"\d+", colour)[:3]]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(one, other):
    lighter, darker = sorted((luminance(one), luminance(other)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def measure(page, values):
    """Each variable's value on the page's body beside the value given for it, both as the browser computes them."""
    return page.evaluate(VARIABLES, values)


def off(found, label, expected):
    """Where a measured variable does not hold the value expected of it."""
    return [f"{label}'s {variable} is {found[variable][0]}, not {found[variable][1]}" for variable in expected
            if found[variable][0] != found[variable][1]]


def role_values(variables):
    """Each variable with its role's value in the brand token file."""
    return {variable: tokens[role] for variable, role in variables.items()}


def mapped(page, label):
    """On the page's body, each variable of the variable map, and each that takes its role without it, holds the role's
    value from the brand token file, and Nextcloud's type keeps its own; the colours read at the contrast WCAG asks for,
    as the page computes them."""
    expected = {**role_values({**VARIABLE_MAP, **MEASURED}), "--border-radius": RADIUS, **NEXTCLOUD_TYPE}
    found = measure(page, {**expected, **dict.fromkeys(KEPT, "")})
    wrong = off(found, label, expected)
    unset = [variable for variable, (value, _) in found.items() if not value]
    if unset:
        return wrong + [f"{label} does not set {', '.join(unset)}"]
    ratios = {(text, background): contrast(found[text][0], found[background][0]) for text, background, _ in CONTRAST}
    wrong += [f"{label}'s {text} reads at {ratios[text, background]:.2f}:1 on {background}, below {least}:1"
              for text, background, least in CONTRAST if ratios[text, background] < least]
    print(f"{label}: {len(found)} variables measured; contrast "
          + ", ".join(f"{text} on {background} {ratio:.2f}" for (text, background), ratio in ratios.items()))
    return wrong


def type_and_top_bar(page, label):
    """The page's first visible heading is in the display font with the manual's letter spacing and its body in the
    body font, with no italic face of either; a logged-in page's top bar shows the gradient from dark violet to violet,
    and the login page's top area none."""
    fonts = {"display": tokens["--aps-font-display"], "text": tokens["--aps-font-body"]}
    shown = page.evaluate(TYPE_AND_TOP_BAR, fonts)
    print(f"{label}: {shown}")
    wrong = [] if shown["heading"] == shown["display"] else [f"{label}'s first heading is in {shown['heading']}"]
    spacing = shown["spacing"]
    if spacing is None or abs(spacing - LETTER_SPACING) > 0.001:
        spaced = "normally" if spacing is None else f"{spacing:.3f}em"
        wrong.append(f"{label}'s first heading is spaced {spaced}, not {LETTER_SPACING}em")
    if shown["body"] != shown["text"]:
        wrong.append(f"{label}'s body is in {shown['body']}")
    if shown["italic"]:
        wrong.append(f"{label} declares italic faces: {shown['italic']}")
    if label.startswith("login"):
        return wrong + ([] if shown["topBar"] == "none" else [f"{label}'s top area shows {shown['topBar']}"])
    found = measure(page, {role: tokens[role] for role in GRADIENT})
    gradient = [found[role][1] for role in GRADIENT]
    stops = re.findall(r"rgba?\([^)]*\)", shown["topBar"] or "")
    if not (shown["topBar"] or "").startswith("linear-gradient(") or stops != gradient:
        wrong.append(f"{label}'s top bar shows {shown['topBar']}, not a gradient through {gradient}")
    return wrong


def themed(page, label):
    """The page wears the theme in the brand token file's colours and fonts."""
    found = page.evaluate(THEMED)
    print(f"{label}: {found}")
    wrong = ([] if found["stylesheet"] else [f"{label} does not load the theme's stylesheet"]) + mapped(page, label)
    wrong += type_and_top_bar(page, label)
    if "Nunito Sans" not in fonts(page):
        wrong.append(f"{label} did not load Nunito Sans")
    if label.startswith("login") and found["establishment"] in NOTHING:
        wrong.append(f"{label} does not name the establishment")
    return wrong


def names_the_establishment(page, label):
    """In this window and in the narrowest that names it, the top bar names the establishment in room right of the
    logo; a narrower window shows the logo alone."""
    shown = {}
    for width in (WINDOW["viewport"]["width"], NAMED_FROM, NAMED_FROM - 1):
        page.set_viewport_size({**WINDOW["viewport"], "width": width})
        shown[width] = page.evaluate(TOP_BAR)
    page.set_viewport_size(WINDOW["viewport"])
    print(f"{label}: the top bar shows, by window width, {shown}")
    wrong = [f"{label}'s top bar does not name {establishment} in room right of the logo at {width} px"
             for width, top_bar in shown.items() if width >= NAMED_FROM
             and (top_bar["name"] != f'"{establishment}"' or top_bar["room"] <= 0)]
    return wrong + ([] if shown[NAMED_FROM - 1]["name"] in NOTHING else
                    [f"{label}'s top bar names the establishment at {NAMED_FROM - 1} px"])


def tab_into(page, place):
    """Press Tab until an element in `place` (a selector) has focus: false if TABS presses do not reach it."""
    for _ in range(TABS):
        page.keyboard.press("Tab")
        if page.evaluate(FOCUSED, place)["inside"]:
            return True
    return False


def ring(page, label, colour, wider=False):
    """The focused element shows the manual's ring, a solid outline in the body's `colour` variable, RING wide or wider,
    set off from it."""
    focused = page.evaluate(FOCUSED, "body")
    expected = measure(page, {colour: ""})[colour][0]
    shows = (f"a {focused['width']}px {focused['style']} outline in {focused['colour']}, offset "
             f"{focused['offset']}px")
    print(f"{label}: {focused['name']} has focus, with {shows}")
    wide = focused["width"] > RING if wider else focused["width"] == RING
    if focused["style"] == "solid" and wide and focused["colour"] == expected and focused["offset"] > 0:
        return []
    return [f"{label}'s focused {focused['name']} has {shows}, not {'wider than ' if wider else ''}{RING}px solid "
            f"{expected}, offset"]


def accessible(page, label):
    """Tabbing to the login form gives the focused control the manual's ring. When the system asks for more contrast,
    secondary text takes the ink, borders the muted colour and the ring grows wider; when it asks for reduced motion,
    Nextcloud's animations take no time."""
    if not tab_into(page, "form"):
        return [f"{TABS} tabs do not reach {label}'s form"]
    wrong = ring(page, label, "--color-primary-element")
    page.emulate_media(contrast="more")
    contrasted = role_values(MORE_CONTRAST)
    wrong += off(measure(page, contrasted), f"{label} with more contrast", contrasted)
    wrong += ring(page, f"{label} with more contrast", "--color-primary-element", wider=True)
    page.emulate_media(contrast="no-preference", reduced_motion="reduce")
    wrong += off(measure(page, REDUCED_MOTION), f"{label} with reduced motion", REDUCED_MOTION)
    page.emulate_media(reduced_motion="no-preference")
    return wrong


def top_bar_ring(page, label):
    """Tabbing to the top bar's account menu gives its button the ring in the backdrop's text colour. Opened from the
    keyboard, the menu moves focus to its white panel, whose entry takes the ring in the primary element colour."""
    if not tab_into(page, "#user-menu"):
        return [f"{TABS} tabs do not reach {label}'s account menu"]
    wrong = ring(page, f"{label}'s top bar", "--color-background-plain-text")
    page.keyboard.press("Enter")
    try:
        page.wait_for_function("() => !!document.activeElement.closest('.header-menu__wrapper')", timeout=10_000)
    except Error:
        return wrong + [f"opening {label}'s account menu from the keyboard does not move focus into its panel"]
    wrong += ring(page, f"{label}'s account menu", "--color-primary-element")
    page.keyboard.press("Escape")
    return wrong


def shipped_fonts():
    """The theme folder's font files are WOFF2 only."""
    placed = machine(f"{NEXTCLOUD} find {PLACED} -type f").split()
    found = [path for path in placed if path.lower().endswith(FONT_FILES)]
    print(f"the theme folder's fonts: {found}")
    other = [path for path in found if not path.lower().endswith(".woff2")]
    return [f"the theme folder ships fonts other than WOFF2: {other}"] if other else []


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
    disabled = core.get("Password reset is disabled", "Password reset is disabled")
    try:
        said = json.loads(answer["text"])
    except ValueError:
        said = {}
    if answer["status"] == 200 and said == {"status": "error", "msg": disabled}:
        return []
    return [f"Nextcloud's email reset does not answer «{disabled}»"]


def help_page(page):
    """From the login page, «¿Contraseña olvidada?» opens the help page: 200 from the server, the establishment's name,
    the brand token file's fonts, no console error nor content-security-policy violation, and a link back to the login
    page that works."""
    found = faults(page)
    forgot = page.locator(f'a[href="{help_link}"]')
    if not forgot.is_visible():
        return [f"the login page does not link {help_link}"]
    label = core["Forgot password?"]
    print(f"login: «{forgot.inner_text().strip()}» links {help_link}")
    wrong = []
    if forgot.inner_text().strip() != label:
        wrong.append(f"the login page's link to {help_link} does not read «{label}»")
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


def inicio_is_the_only_home(page):
    """Login landed on «Inicio», and Nextcloud's Dashboard is off: the server lists it disabled, and its address does
    not open it."""
    page.wait_for_load_state()
    landed = page.url
    disabled = json.loads(occ("app:list --output=json"))["disabled"]
    answer = page.goto(f"{suite}/apps/{DASHBOARD}/")
    opened = page.locator(f"#app-{DASHBOARD}").count() > 0
    print(f"login landed on {landed}; the Dashboard is {'off' if DASHBOARD in disabled else 'on'}, and its address "
          f"answers {answer.status}{', opening it' if opened else ''}")
    return ([f"login lands on {landed}, not «Inicio»"] if f"/apps/{APP}/" not in landed else []) + (
        [] if DASHBOARD in disabled else ["Nextcloud's Dashboard is on"]) + (
        ["the Dashboard's address still opens it"] if opened else [])


def inicio_in_spanish(page):
    """«Inicio» speaks IntraVox's own Spanish: the edit button a staff member sees says «Editar página», and no
    «Edit page» shows."""
    try:
        page.get_by_text("Editar página", exact=True).first.wait_for(state="visible", timeout=30_000)
        shown = True
    except Error:
        shown = False
    english = page.get_by_text("Edit page", exact=True).count()
    print(f"«Inicio» shows «Editar página»: {'yes' if shown else 'no'}; «Edit page»: {english}")
    return ([] if shown else ["«Inicio» does not show IntraVox's «Editar página»"]) + (
        ["«Inicio» shows IntraVox's English «Edit page»"] if english else [])


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
    problems += login_page(page, "login") + accessible(page, "login") + email_reset_refused(page) + help_page(page)
    log_in(page, suite, PERSON, password)
    problems += inicio_is_the_only_home(page)
    for label in PAGES:
        open_page(page, label)
        problems += themed(page, label)
        if label == "inicio":
            problems += names_the_establishment(page, label) + inicio_in_spanish(page) + top_bar_ring(page, label)
        problems += shows(page, label)
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
    problems += shipped_fonts()
    support = occ("config:app:get support enabled")
    print(f"support app after the restart: {support.strip()}")
    if support.strip() != "no":
        problems.append("the support app is on again after Nextcloud's restart")

if problems:
    sys.exit("\n".join(problems))
print("the theme is on every page, with no «Nextcloud», and survives a restart of Nextcloud's container, which "
      "rebuilds Talk's browser texts")
