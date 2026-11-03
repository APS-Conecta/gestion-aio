# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ISP's intermediate certificate: www.ispch.gob.cl, whose feeds carry the national sanitary alerts, serves only
its leaf, which browsers complete and Nextcloud's own requests cannot. Nextcloud trusts the CA that issued it."""

NAME = "Certificado del ISP"
NEEDS = ("start",)
HOST = "www.ispch.gob.cl"


def run(context):
    context.suite.trust_issuer(HOST)
