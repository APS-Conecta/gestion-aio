# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The suite port: the only code that talks to AIO, the APS name service and Nextcloud's command line (occ).

The Provisionador is built with one: `Suite` in production, `FakeSuite` in the wizard-API tests. A screen
that needs the suite adds its methods to both, and the fake records what it was asked so the tests can
assert the suite state. «Centro» asks the suite for nothing yet.
"""


class Suite:
    """The real adapter, driving AIO, the name service and occ on this server."""


class FakeSuite:
    """The in-memory suite of the wizard-API tests."""
