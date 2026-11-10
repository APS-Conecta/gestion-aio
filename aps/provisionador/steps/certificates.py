# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ISP's intermediate certificate: www.ispch.gob.cl, whose feeds carry the national sanitary alerts, serves only
its leaf, which browsers complete and Nextcloud's own requests cannot. Nextcloud trusts the CA that issued it from the
copy the suite ships, as the site goes down now and then while its certificate keeps working; when the site's leaf moves
to another CA, a release ships that CA's copy in place of this one.
Nextcloud also trusts the CAs of AIO's own folder of trusted CAs, which AIO gives Talk: a test build's Let's Encrypt
staging roots, so server-side calls through the suite's name, Talk's signaling among them, verify its certificate as a
release's verify Let's Encrypt's. A release has none."""

from pathlib import Path

NAME = "Certificado del ISP"
NEEDS = ("start",)
INTERMEDIATE = Path(__file__).resolve().parents[1] / "gsgccr6alphasslca2025.pem"  # the copy the suite ships


def run(context):
    context.suite.trust_issuer(INTERMEDIATE.name, INTERMEDIATE.read_text(encoding="ascii"))
    context.suite.trust_cacerts()
