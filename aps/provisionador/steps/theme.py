# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The APS Conecta theme and branding. The theme folder (aps/theme/apsconecta) goes into Nextcloud's themes folder,
which upstream's updates keep, with the brand folder (the brand token file, its fonts and the favicon), its art rendered
in the tokens' colours, the establishment's name in site.css and in the lost-password help page, and the text overrides.
Theming then says «APS Conecta Gestión» with the token file's colours, the theme's logo and the dark violet as backdrop,
light only. Nextcloud's own voice goes quiet: its announcements, first-login slides, usage survey and support apps, its
help links, its «get your own free account» link, its update banners and its iOS app banner are off, and a new person's
home starts empty instead of with its sample files. «¿Contraseña olvidada?» opens the help page, which Nextcloud's web
server serves from the theme folder, in place of Nextcloud's email reset, which then answers that reset is disabled: the
suite sends no email, and a person gets a temporary password in person. Everyone's personal settings link the suite's
source code and its licence.

The text overrides (aps/theme/texts.json) are Spanish translations, Nextcloud's and its apps', that name the suite
«Nextcloud», saying «APS Conecta Gestión» instead («Nextcloud Talk» is «Talk», and Euro-Office's «Nextcloud Office» is
«Office»); one that names Nextcloud's company, editions, versions or community keeps upstream's. Nextcloud's server adds
a theme's translations to its own on every screen and email it renders, and its browser gets them as
aps/images/nextcloud/browser-texts.php builds them.
"""

import html
import json
from pathlib import Path

import brand
from steps import differences

NAME = "Tema y marca"
NEEDS = ("start",)
THEME = "apsconecta"
SOURCE = Path(__file__).resolve().parents[2] / "theme"  # aps/theme, or theme/ in the bundle
PLACED = f"/var/www/html/themes/{THEME}"
HELP = "contrasena.html"  # the lost-password help page, in the theme folder
SUITE = "APS Conecta Gestión"
SITE = "https://apsconecta.cl"  # where the suite's name links on the login page
OFF = ("nextcloud_announcements", "firstrunwizard", "survey_client", "support")
IMAGES = {"logo": "core/img/logo.svg", "logoheader": "core/img/logoheader.svg", "favicon": "brand/favicon.svg"}
IMPORTED = {"system": {"theme": THEME, "enforce_theme": "light", "knowledgebaseenabled": False,
                       "simpleSignUpLink.shown": False, "updatechecker": False, "skeletondirectory": "",
                       "customclient_ios_appid": ""},
            "apps": {"theming": {"productName": SUITE}}}


def theming():
    """What occ theming:config sets, in the order it sets it: the backdrop is the background colour."""
    tokens = brand.tokens()
    return {"name": SUITE, "url": SITE, "slogan": brand.MOTTO, "primary_color": tokens["--aps-violet"],
            "background_color": tokens["--aps-violet-dark"], "disable-user-theming": "yes",
            **{image: f"{PLACED}/{path}" for image, path in IMAGES.items()}, "background": "backgroundColor"}


def imported(context):
    """What occ config:import sets: IMPORTED, and the help page's address on the suite as the lost-password link."""
    link = f"https://{context.name}/themes/{THEME}/{HELP}"
    return {**IMPORTED, "system": {**IMPORTED["system"], "lost_password_link": link}}


def settings(context):
    """What Nextcloud holds once the step is done, as occ config:list answers it."""
    held = {key: True if key == "disable-user-theming" else value for key, value in theming().items()
            if key not in IMAGES and key != "background"}
    return {"system": imported(context)["system"],
            "apps": {"theming": {**IMPORTED["apps"]["theming"], **held, "backgroundMime": "backgroundColor",
                                 **{f"{image}Mime": "image/svg+xml" for image in IMAGES}},
                     **{app: {"enabled": "no"} for app in OFF}}}


def files(context):
    """The theme folder's files by their path in it."""
    folder = SOURCE / THEME
    found = {path.relative_to(folder).as_posix(): path for path in folder.rglob("*") if path.is_file()}
    found |= {f"brand/{path.name}": path for path in brand.FOLDER.iterdir()}
    placed = {name: path.read_bytes() for name, path in found.items()}
    placed |= {name: brand.render(path.read_text(encoding="utf-8")).encode() for name, path in found.items()
               if name.endswith(".svg")}
    for folder, overrides in json.loads((SOURCE / "texts.json").read_text(encoding="utf-8")).items():
        placed[f"{folder}/l10n/es.json"] = json.dumps({"translations": overrides}, ensure_ascii=False).encode()
    quoted = context.centre["short_name"].replace("\\", "\\\\").replace('"', '\\"')
    placed["core/css/site.css"] = f':root {{ --aps-establishment: "{quoted}"; }}\n'.encode()
    named = html.escape(context.centre["short_name"])
    placed[HELP] = found[HELP].read_text(encoding="utf-8").replace("{establishment}", named).encode()
    return placed


def run(context):
    context.suite.theme(THEME, files(context))
    context.suite.browser_texts()
    context.suite.occ("config:import", stdin=json.dumps(imported(context)))
    for key, value in theming().items():
        context.suite.occ("theming:config", key, value)
    for app in OFF:
        context.suite.occ("app:disable", app)
    return check(context)


def check(context):
    return differences(context.suite, settings(context))
