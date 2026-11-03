# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The real suite port's answers to the name service's refusals and outages, with urlopen patched."""

import io
import json
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import suite


def refusal(code):
    return urllib.error.HTTPError("https://names.test/claim", code, "refused", {}, io.BytesIO(b'{"error": "no"}'))


class NameServiceAnswers(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        services = Path(tmp.name) / "services.json"
        services.write_text(json.dumps({"url": "https://names.test", "suffix": "gestion.apsconecta.cl",
                                        "acme_ca": "https://acme.test/directory"}), encoding="utf-8")
        self.suite = suite.Suite(services, Path(tmp.name) / "name-service.json")

    def call(self, path, outcome):
        with mock.patch("suite.urllib.request.urlopen", side_effect=outcome), \
                mock.patch("sys.stderr", io.StringIO()):
            return self.suite.call(path, {})

    def message(self, path, outcome):
        with self.assertRaises(suite.Unreachable) as raised:
            self.call(path, outcome)
        return str(raised.exception)

    def test_a_name_in_use_is_taken(self):
        with self.assertRaises(suite.Taken):
            self.call("/claim", refusal(409))

    def test_the_claim_limits_ask_to_retry_later_on_a_claim_only(self):
        self.assertEqual(self.message("/claim", refusal(429)), suite.LIMITED)
        self.assertEqual(self.message("/address", refusal(429)), suite.REFUSED.format(code=429))

    def test_any_other_refusal_names_its_status_and_asks_to_contact_aps_conecta(self):
        for code in (400, 401, 403, 404):
            self.assertEqual(self.message("/claim", refusal(code)), suite.REFUSED.format(code=code))
        self.assertIn("HTTP 401", suite.REFUSED.format(code=401))
        self.assertIn("Contacte a APS Conecta", suite.REFUSED.format(code=401))

    def test_a_server_error_or_a_network_failure_is_an_outage(self):
        for outcome in (refusal(500), refusal(502), urllib.error.URLError("no route"), socket.timeout("timed out")):
            self.assertEqual(self.message("/claim", outcome), suite.UNREACHABLE, outcome)


if __name__ == "__main__":
    unittest.main()
