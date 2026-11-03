# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The APS custom menu: the Custom Menu store app (side_menu), which the apps step installs from the Suite manifest,
in the brand token file's dark violet, as old gestion set it; the theme shows the header logo in its panel. The step
installs and enables nothing: without the app, it fails and says so."""

import json

import brand
from steps import configure, differences

NAME = "Menú personalizado"
NEEDS = ("apps",)
APP = "side_menu"
MISSING = ("La aplicación del menú personalizado (Custom Menu, side_menu) no está instalada y activa en el servidor de "
           "aplicaciones. Presione «Reintentar»; si vuelve a ocurrir, contacte a APS Conecta.")


def settings():
    violet = brand.tokens()["--aps-violet-dark"]
    return {"apps": {APP: {"background-color": violet, "background-color-to": violet}}}


def enabled(context):
    return json.loads(context.suite.occ("config:list"))["apps"].get(APP, {}).get("enabled") == "yes"


def run(context):
    return configure(context.suite, settings()) if enabled(context) else [MISSING]


def check(context):
    return differences(context.suite, settings()) if enabled(context) else [MISSING]
