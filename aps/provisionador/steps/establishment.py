# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The centre as Estadística reads it from its app settings, which it never writes: the DEIS code, the register's type
and the comuna's CUT, as old gestion's phase 16 set them. They are set whether or not the operator ticked Estadística,
so an Estadística enabled after the install finds them. Estadística reads a code that is not six digits as no
establishment, and says nothing: the step stops instead, naming the code."""

import re

from steps import configure, differences

NAME = "Establecimiento en Estadística"
NEEDS = ("start",)
NOT_SIX_DIGITS = ("El código DEIS del establecimiento, «{}», no tiene seis dígitos, y Estadística no lo reconocería. "
                  "Contacte a APS Conecta.")


def settings(context):
    centre = context.centre
    return {"apps": {"estadistica": {"deis_code": centre["code"], "establishment_type": centre["type"],
                                     "comuna_cut": centre["comuna_code"]}}}


def run(context):
    code = context.centre["code"]
    if not re.fullmatch("[0-9]{6}", code):
        return [NOT_SIX_DIGITS.format(code)]
    return configure(context.suite, settings(context))


def check(context):
    return differences(context.suite, settings(context))
