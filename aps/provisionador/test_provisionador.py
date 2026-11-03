# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wizard-API tests: the Provisionador's HTTP API on loopback against the fake suite port.

Each test runs a real Provisionador over a throwaway state directory and a register built here, and
mints links with the real `link` command, as the bootstrap does. No browser, no Docker.
"""

import hashlib
import http.client
import json
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import provisionador
from suite import FakeSuite

HERE = Path(__file__).resolve().parent

# Fixture codes only: the DEIS-code gate proves no real register code is named here.
HEADER = "codigo,tipo,nombre,direccion,comuna_codigo,comuna,region_codigo,region,servicio_salud,dependencia,latitud,longitud\n"
REGISTER = HEADER + """\
999001,CESFAM,Centro de Salud Familiar Los Castaños,Calle Uno 100,13101,Santiago,13,Metropolitana de Santiago,Servicio de Salud Metropolitano Central,Municipal,-33.45,-70.66
999002,CECOSF,Centro Comunitario de Salud Familiar Estrecho,Avenida Dos 200,12101,Punta Arenas,12,Magallanes y de la Antártica Chilena,Servicio de Salud Magallanes,Municipal,-53.16,-70.91
999003,SAPU,SAPU Rapa Nui,Calle Tres 300,05201,Isla de Pascua,05,Valparaíso,Servicio de Salud Viña del Mar Quillota,Municipal,-27.15,-109.43
999004,CESFAM,Centro de Salud Familiar Doctor Juan Pérez,"Calle Cuatro 400, Villa Sur",13101,Santiago,13,Metropolitana de Santiago,Servicio de Salud Metropolitano Central,Municipal,-33.47,-70.64
"""


def fingerprint(pem):
    h = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


class Link:
    """What the `link` command printed: the URL and the certificate fingerprint."""

    def __init__(self, output):
        self.output = output
        self.url = re.search(r"https://\S+", output).group(0)
        self.fingerprint = re.search(r"(?:[0-9A-F]{2}:){31}[0-9A-F]{2}", output).group(0)


class Browser:
    """A browser on the operator's laptop: trusts exactly the certificate the server presents for its
    address, keeps the session cookie, and opens a link the way the page does."""

    def __init__(self, url):
        parts = urlsplit(url)
        self.host, self.port = parts.hostname, parts.port
        pem = ssl.get_server_certificate((self.host, self.port))
        self.fingerprint = fingerprint(pem)
        self.context = ssl.create_default_context(cadata=pem)
        self.cookie = None
        self.headers = {}

    def request(self, method, path, body=None, content_type="application/json"):
        """`body` is sent as JSON, or as is when it is bytes."""
        headers = {"Cookie": self.cookie} if self.cookie else {}
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers["Content-Type"] = content_type
        conn = http.client.HTTPSConnection(self.host, self.port, context=self.context, timeout=10)
        try:
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            self.headers = dict(response.getheaders())
        finally:
            conn.close()
        if "Set-Cookie" in self.headers:
            self.cookie = self.headers["Set-Cookie"].split(";", 1)[0]
        is_json = self.headers.get("Content-Type", "").startswith("application/json")
        return response.status, json.loads(raw) if is_json else raw

    def get(self, path):
        return self.request("GET", path)

    def open(self, url):
        return self.request("POST", "/api/session", {"link": urlsplit(url).fragment})


class WizardTest(unittest.TestCase):
    REGISTER = REGISTER

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"
        self.register = Path(tmp.name) / "establecimientos-deis-2026-07-23.csv"
        self.register.write_text(self.REGISTER, encoding="utf-8")
        self.suite = FakeSuite()
        self.server = None
        self.addCleanup(self.stop)
        self.start()

    def start(self, port=0):
        """Start the Provisionador over the state directory, as systemd does at boot."""
        self.server = provisionador.Provisionador(self.state, self.register, self.suite).server(("127.0.0.1", port))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            self.server = None

    def link(self):
        r = subprocess.run([sys.executable, "-B", str(HERE / "provisionador.py"), "link", "--state", str(self.state)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return Link(r.stdout)

    def operator(self):
        """A browser that opened a fresh link."""
        link = self.link()
        browser = Browser(link.url)
        status, _ = browser.open(link.url)
        self.assertEqual(status, 200)
        return browser


class OneTimeLink(WizardTest):
    def test_the_link_names_the_running_wizard_and_carries_its_secret_in_the_fragment(self):
        url = urlsplit(self.link().url)
        self.assertEqual((url.hostname, url.port, url.path), ("127.0.0.1", self.server.server_address[1], "/"))
        self.assertGreaterEqual(len(url.fragment), 43)

    def test_every_wizard_call_without_a_session_is_refused(self):
        browser = Browser(self.link().url)
        for method, path in (("GET", "/api/state"), ("GET", "/api/register"),
                             ("GET", "/api/establishments?name=castanos"), ("POST", "/api/centre")):
            status, body = browser.request(method, path, {} if method == "POST" else None)
            self.assertEqual(status, 401, path)
            self.assertIn("enlace", body["error"])
        browser.cookie = f"{provisionador.COOKIE}=forged"
        self.assertEqual(browser.get("/api/state")[0], 401)

    def test_the_secret_opens_one_session_on_first_use_only(self):
        link = self.link()
        browser = Browser(link.url)
        self.assertEqual(browser.open(link.url)[0], 200)
        cookie = browser.headers["Set-Cookie"]
        for attribute in ("Path=/", "Secure", "HttpOnly", "SameSite=Strict"):
            self.assertIn(attribute, cookie)
        self.assertEqual(browser.get("/api/state")[0], 200)
        self.assertEqual(browser.open(link.url)[0], 200, "the browser that opened it keeps its session")

        stranger = Browser(link.url)
        status, body = stranger.open(link.url)
        self.assertEqual(status, 401)
        self.assertIn("vuelva a ejecutar", body["error"])
        self.assertEqual(stranger.get("/api/state")[0], 401)

    def test_a_link_opened_by_a_browser_with_a_session_is_spent_too(self):
        browser = self.operator()
        link = self.link()
        self.assertEqual(browser.open(link.url)[0], 200)
        self.assertEqual(Browser(link.url).open(link.url)[0], 401)

    def test_a_post_that_is_not_json_is_refused_so_other_origins_need_a_preflight(self):
        browser = self.operator()
        status, body = browser.request("POST", "/api/centre", b'{"code": "999001"}', content_type="text/plain")
        self.assertEqual(status, 415)
        self.assertIn("JSON", body["error"])
        self.assertIsNone(browser.get("/api/state")[1]["centre"])

    def test_a_body_nested_too_deep_is_refused(self):
        browser = Browser(self.link().url)
        status, body = browser.request("POST", "/api/session", b"[" * 60000)
        self.assertEqual(status, 400)
        self.assertIn("JSON", body["error"])

    def test_rerunning_the_command_prints_a_fresh_link_and_the_unused_one_stops_working(self):
        first, second = self.link(), self.link()
        self.assertNotEqual(first.url, second.url)
        self.assertEqual(Browser(first.url).open(first.url)[0], 401)
        self.assertEqual(Browser(second.url).open(second.url)[0], 200)

    def test_the_printed_fingerprint_is_the_served_certificate_and_the_one_the_wizard_shows(self):
        link = self.link()
        browser = Browser(link.url)
        self.assertEqual(link.fingerprint, browser.fingerprint)
        browser.open(link.url)
        status, state = browser.get("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["fingerprint"], link.fingerprint)


class Page(WizardTest):
    def test_the_link_opens_the_spanish_page_and_everything_it_loads(self):
        browser = Browser(self.link().url)
        status, page = browser.get("/")
        self.assertEqual(status, 200)
        page = page.decode()
        self.assertIn('<html lang="es-CL">', page)
        self.assertIn("Centro", page)
        assets = re.findall(r'(?:src|href)="(/[^"]+)"', page)
        self.assertTrue(assets)
        for asset in assets:
            self.assertEqual(browser.get(asset)[0], 200, asset)
        self.assertEqual(browser.get("/provisionador.py")[0], 404)


class CentroSearch(WizardTest):
    def codes(self, browser, **query):
        status, body = browser.get("/api/establishments?" + urlencode(query))
        self.assertEqual(status, 200, body)
        return [row["code"] for row in body["rows"]]

    def test_the_register_lists_regions_north_to_south_their_comunas_and_the_types(self):
        status, register = self.operator().get("/api/register")
        self.assertEqual(status, 200)
        self.assertEqual(register["date"], "2026-07-23")
        self.assertEqual([r["code"] for r in register["regions"]], ["05", "13", "12"])
        self.assertIn({"code": "12101", "name": "Punta Arenas", "region": "12"}, register["comunas"])
        self.assertEqual(register["types"], [{"code": "CESFAM", "name": "Centro de Salud Familiar"},
                                             {"code": "CECOSF", "name": "Centro Comunitario de Salud Familiar"},
                                             {"code": "SAPU", "name": "Servicio de Atención Primaria de Urgencia"}])

    def test_search_by_region_comuna_type_or_name_returns_register_rows(self):
        browser = self.operator()
        self.assertEqual(self.codes(browser, region="12"), ["999002"])
        self.assertEqual(self.codes(browser, comuna="13101"), ["999004", "999001"], "by name")
        self.assertEqual(self.codes(browser, type="SAPU"), ["999003"])
        self.assertEqual(self.codes(browser, name="CASTANOS"), ["999001"])
        self.assertEqual(self.codes(browser, name="juan perez", type="CESFAM", comuna="13101"), ["999004"])
        self.assertEqual(self.codes(browser, region="12", type="SAPU"), [])
        status, body = browser.get("/api/establishments?" + urlencode({"name": "los castaños"}))
        self.assertEqual(body, {"total": 1, "rows": [{
            "code": "999001", "type": "CESFAM", "name": "Centro de Salud Familiar Los Castaños",
            "address": "Calle Uno 100", "comuna": "Santiago", "region": "Metropolitana de Santiago",
            "point": {"latitude": -33.45, "longitude": -70.66}}]})


class CentroChoice(WizardTest):
    def choose(self, browser, **body):
        return browser.request("POST", "/api/centre", body)

    def test_choosing_an_establishment_stores_its_identity_and_official_point(self):
        browser = self.operator()
        self.assertIsNone(browser.get("/api/state")[1]["centre"])
        status, body = self.choose(browser, code="999004")
        self.assertEqual(status, 200, body)
        official = {"latitude": -33.47, "longitude": -70.64}
        self.assertEqual(body["centre"], {
            "code": "999004", "type": "CESFAM", "name": "Centro de Salud Familiar Doctor Juan Pérez",
            "address": "Calle Cuatro 400, Villa Sur", "comuna_code": "13101", "comuna": "Santiago",
            "region_code": "13", "region": "Metropolitana de Santiago",
            "health_service": "Servicio de Salud Metropolitano Central", "dependency": "Municipal",
            "official_point": official, "point": official, "timezone": "America/Santiago"})
        self.assertEqual(browser.get("/api/state")[1]["centre"], body["centre"])

    def test_a_moved_point_is_stored_beside_the_official_one(self):
        browser = self.operator()
        status, body = self.choose(browser, code="999001", point={"latitude": -33.4512, "longitude": -70.6634})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["centre"]["point"], {"latitude": -33.4512, "longitude": -70.6634})
        self.assertEqual(body["centre"]["official_point"], {"latitude": -33.45, "longitude": -70.66})
        self.assertEqual(browser.get("/api/state")[1]["centre"]["point"], body["centre"]["point"])

    def test_an_unknown_establishment_or_an_invalid_point_is_refused_and_changes_nothing(self):
        browser = self.operator()
        self.assertEqual(self.choose(browser, code="999999")[0], 404)
        self.assertEqual(self.choose(browser, code=999001)[0], 404)
        self.assertEqual(self.choose(browser, code=["999001"])[0], 404)
        for point in ({"latitude": 91, "longitude": 0}, {"latitude": 0, "longitude": -181},
                      {"latitude": "-33", "longitude": -70}, {"latitude": True, "longitude": -70},
                      {"latitude": -33}, "-33,-70"):
            status, body = self.choose(browser, code="999001", point=point)
            self.assertEqual(status, 400, point)
            self.assertIn("punto", body["error"])
        self.assertIsNone(browser.get("/api/state")[1]["centre"])

    def test_the_timezone_follows_magallanes_and_isla_de_pascua(self):
        browser = self.operator()
        for code, timezone in (("999002", "America/Punta_Arenas"), ("999003", "Pacific/Easter"),
                               ("999001", "America/Santiago")):
            status, body = self.choose(browser, code=code)
            self.assertEqual((status, body["centre"]["timezone"]), (200, timezone), code)



class Resume(WizardTest):
    def test_a_restart_keeps_the_session_the_saved_centre_and_the_certificate(self):
        link = self.link()
        browser = Browser(link.url)
        browser.open(link.url)
        browser.request("POST", "/api/centre", {"code": "999002"})
        port = self.server.server_address[1]
        self.stop()
        self.start(port)
        status, state = browser.get("/api/state")
        self.assertEqual((status, state["centre"]["code"]), (200, "999002"))
        self.assertEqual(self.link().fingerprint, link.fingerprint)

    def test_a_rerun_opens_a_fresh_session_on_the_same_state(self):
        first = self.operator()
        first.request("POST", "/api/centre", {"code": "999003"})
        second = self.operator()
        self.assertEqual(second.get("/api/state")[1]["centre"]["code"], "999003")
        self.assertEqual(first.get("/api/state")[0], 200)


class LargeRegister(WizardTest):
    REGISTER = HEADER + "".join(
        f"99{i:04},PSR,Posta de Salud Rural {i:02},Camino {i},13101,Santiago,13,Metropolitana de Santiago,"
        f"Servicio de Salud Metropolitano Central,Municipal,-33.4,-70.6\n" for i in range(1100, 1160))

    def test_a_search_answers_the_first_fifty_rows_and_the_total(self):
        status, body = self.operator().get("/api/establishments?type=PSR")
        self.assertEqual((status, body["total"], len(body["rows"])), (200, 60, 50))


if __name__ == "__main__":
    unittest.main()
