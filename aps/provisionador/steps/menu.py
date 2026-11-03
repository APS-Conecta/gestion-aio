# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The APS custom menu: the Custom Menu store app (side_menu), which the apps step installs from the Suite manifest and
holds enabled, in the brand token file's dark violet, as old gestion set it; the theme shows the header logo in its
panel."""

import brand
from steps import configure, differences

NAME = "Menú personalizado"
NEEDS = ("apps",)


def settings():
    violet = brand.tokens()["--aps-violet-dark"]
    return {"apps": {"side_menu": {"background-color": violet, "background-color-to": violet}}}


def run(context):
    return configure(context.suite, settings())


def check(context):
    return differences(context.suite, settings())
