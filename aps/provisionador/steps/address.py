# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The suite's dirección: its name points at the server's inside address, and the suite answers there once our Caddy
image got the name's certificate through the name service's DNS challenge. The end-of-install check waits for that
answer, so at «Listo» staff can open the suite and the page can tell whether this computer reaches it."""

from steps import until

NAME = "Dirección de la suite"
NEEDS = ()
WAIT = 10 * 60  # seconds; a certificate usually takes a minute or two
SILENT = ("La suite todavía no responde en https://{} con su certificado. Presione «Reintentar» en unos minutos; si "
          "vuelve a ocurrir, contacte a APS Conecta.")


def run(context):
    context.suite.publish(context.inside)


def check(context):
    answers = until(lambda: context.suite.answers(context.name, context.inside), bool, WAIT)
    return [] if answers else [SILENT.format(context.name)]
