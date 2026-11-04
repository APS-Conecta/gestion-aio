# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The APS custom menu: the Custom Menu store app (side_menu) of the Suite manifest, which the step ensures, in the
brand token file's dark violet, as old gestion set it; the theme shows the header logo in its panel. Without the app,
the step fails naming it, and no other app's failure holds it back."""

import brand
from steps import apps, configure, differences

NAME = "Menú personalizado"
NEEDS = ("start",)


def settings():
    violet = brand.tokens()["--aps-violet-dark"]
    return {"apps": {"side_menu": {"background-color": violet, "background-color-to": violet}}}


def run(context):
    if failed := apps.ensure(context, "side_menu"):
        return failed
    return configure(context.suite, settings())


def check(context):
    return differences(context.suite, settings())
