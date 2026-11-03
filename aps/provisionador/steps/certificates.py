# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ISP's intermediate certificate: www.ispch.gob.cl, whose feeds carry the national sanitary alerts, serves only
its leaf, which browsers complete and Nextcloud's own requests cannot. Nextcloud trusts the CA that issued it.
Nextcloud also trusts the CAs of AIO's own folder of trusted CAs, which AIO gives Talk: a test build's Let's Encrypt
staging roots, so server-side calls through the suite's name, Talk's signaling among them, verify its certificate as a
release's verify Let's Encrypt's. A release has none."""

NAME = "Certificado del ISP"
NEEDS = ("start",)
HOST = "www.ispch.gob.cl"


def run(context):
    context.suite.trust_issuer(HOST)
    context.suite.trust_cacerts()
