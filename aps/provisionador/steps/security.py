# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sessions on shared clinical workstations, and the audit log, as old gestion provisioned them: no «Recordarme»
(a lifetime of 0 hides it and refuses it), a session ends after 15 minutes without use (no keepalive renews it from an
open tab), and admin_audit records administrative actions."""

from steps import configure, differences

NAME = "Sesiones y registro de auditoría"
NEEDS = ("start",)
AUDIT = "admin_audit"
SETTINGS = {"system": {"remember_login_cookie_lifetime": 0, "session_lifetime": 900, "session_keepalive": False},
            "apps": {AUDIT: {"enabled": "yes"}}}


def run(context):
    context.suite.occ("app:enable", AUDIT)
    configure(context.suite, SETTINGS)


def check(context):
    return differences(context.suite, SETTINGS)
