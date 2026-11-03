# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The suite answers at its dirección: our Caddy image got the name's certificate through the name service's DNS
challenge, so at «Listo» staff can open the suite and the page can tell whether this computer reaches it."""

from steps import until
from suite import Unreachable

NAME = "Dirección de la suite"
NEEDS = ("start",)
WAIT = 10 * 60  # seconds: a certificate usually takes one or two
SILENT = ("La suite todavía no responde en https://{} con su certificado. Presione «Reintentar» en unos minutos; si "
          "vuelve a ocurrir, contacte a APS Conecta.")


def answers(context):
    return context.suite.answers(context.name, context.inside)


def run(context):
    if not until(lambda: answers(context), bool, WAIT):
        raise Unreachable(SILENT.format(context.name))


def check(context):
    return [] if answers(context) else [SILENT.format(context.name)]
