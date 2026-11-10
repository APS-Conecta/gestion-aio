# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wizard-API tests: the Provisionador's HTTP API on loopback against the fake suite port.

Each test runs a real Provisionador over a throwaway state directory and a register built here, and
mints links with the real `link` command, as the bootstrap does. No browser, no Docker.
"""

import base64
import hashlib
import http.client
import io
import itertools
import json
import os
import re
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode, urlsplit

import brand
import provisionador
import runner
from steps import apps, people, start
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
                             ("GET", "/api/establishments?name=castanos"), ("POST", "/api/centre"),
                             ("POST", "/api/address"), ("POST", "/api/teams"), ("POST", "/api/retire"),
                             ("POST", "/api/install")):
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

    def test_the_page_takes_its_palette_and_fonts_from_the_brand_token_file(self):
        browser = Browser(self.link().url)
        page = browser.get("/")[1].decode()
        self.assertIn('<link rel="stylesheet" href="/tokens.css">', page)
        status, tokens = browser.get("/tokens.css")
        self.assertEqual((status, tokens), (200, (brand.FOLDER / "tokens.css").read_bytes()))
        fonts = re.findall(r'url\("([^"]+)"\)', tokens.decode())
        self.assertEqual(sorted(fonts), ["fraunces.woff2", "nunito-sans.woff2"])
        for font in fonts:
            self.assertEqual(browser.get(f"/{font}")[0], 200, font)

    def test_the_wizard_serves_the_motto_its_header_shows(self):
        status, state = self.operator().get("/api/state")
        self.assertEqual((status, state["motto"]), (200, MOTTO))


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
            "short_name": "CESFAM Doctor Juan Pérez", "address": "Calle Cuatro 400, Villa Sur", "comuna_code": "13101",
            "comuna": "Santiago", "region_code": "13", "region": "Metropolitana de Santiago",
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


class HiddenTestEstablishment(WizardTest):
    """A test establishment beside the register, in establecimientos-prueba.csv: the API takes its code, as a release's
    acceptance run sends it, and «Dirección» names it from the slug table, but «Centro» never lists it."""

    TESTS = HEADER + "999009,CESFAM,Centro de Salud Familiar Prueba,Sin dirección,13132,Vitacura,13,Metropolitana de " \
                     "Santiago,Servicio de Salud Metropolitano Oriente,Municipal,-33.39,-70.59\n"

    def setUp(self):
        super().setUp()
        self.register.with_name("establecimientos-prueba.csv").write_text(self.TESTS, encoding="utf-8")
        self.stop()
        self.start()
        self.browser = self.operator()

    def test_the_api_takes_its_code_and_direccion_claims_its_name(self):
        status, body = self.browser.request("POST", "/api/centre", {"code": "999009"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["centre"]["name"], "Centro de Salud Familiar Prueba")
        self.assertEqual(self.browser.get("/api/state")[1]["address"],
                         {"name": "cesfam-prueba.gestion.apsconecta.cl", "claimed": False})
        status, body = self.browser.request("POST", "/api/address", {})
        self.assertEqual((status, self.suite.claims), (200, ["999009"]), body)

    def test_centro_never_lists_it(self):
        for query in ({}, {"name": "prueba"}, {"region": "13"}, {"comuna": "13132"}, {"type": "CESFAM"}):
            status, body = self.browser.get("/api/establishments?" + urlencode(query))
            self.assertEqual(status, 200, body)
            self.assertNotIn("999009", [row["code"] for row in body["rows"]], query)
        self.assertEqual(self.browser.get("/api/establishments")[1]["total"], 4)
        register = self.browser.get("/api/register")[1]
        self.assertNotIn("13132", [comuna["code"] for comuna in register["comunas"]])


NAME = "cesfam-los-castanos.gestion.apsconecta.cl"


class Direccion(WizardTest):
    """«Dirección»: the centre's name from the slug table, claimed from the APS name service through the suite port."""

    def setUp(self):
        super().setUp()
        self.browser = self.operator()

    def choose(self, code="999001", **body):
        return self.browser.request("POST", "/api/centre", {"code": code, **body})

    def claim(self, **body):
        return self.browser.request("POST", "/api/address", body)

    def address(self):
        return self.browser.get("/api/state")[1]["address"]

    def test_the_name_is_the_centres_from_the_slug_table_and_is_not_editable(self):
        self.assertIsNone(self.address())
        for code, slug in (("999002", "cecosf-estrecho"), ("999003", "sapu-rapa-nui"),
                           ("999004", "cesfam-doctor-juan-perez"), ("999001", "cesfam-los-castanos")):
            self.choose(code)
            self.assertEqual(self.address(), {"name": f"{slug}.gestion.apsconecta.cl", "claimed": False})
        status, body = self.claim(name="otro-nombre.gestion.apsconecta.cl")
        self.assertEqual((status, body["address"]["name"]), (200, NAME))

    def test_there_is_no_claim_before_the_centre_is_chosen(self):
        status, body = self.claim()
        self.assertEqual(status, 409)
        self.assertIn("centro", body["error"])
        self.assertEqual(self.suite.claims, [])

    def test_the_claim_asks_nothing_sets_the_suite_to_the_name_and_publishes_the_inside_address(self):
        self.choose()
        status, body = self.claim()
        self.assertEqual((status, body), (200, {"address": {"name": NAME, "claimed": True}}))
        self.assertEqual((self.suite.claims, self.suite.domain), (["999001"], NAME))
        self.assertEqual(self.suite.published, [provisionador.lan_ip()])
        self.assertEqual(self.address(), body["address"])

    def test_a_name_in_use_says_aps_conecta_can_release_it_and_a_retry_claims_it_once_released(self):
        self.suite.in_use = {"999001"}
        self.choose()
        status, body = self.claim()
        self.assertEqual(status, 409)
        for text in (NAME, "en uso", "APS Conecta puede liberarla", "Reintentar"):
            self.assertIn(text, body["error"])
        self.assertEqual((self.suite.claims, self.suite.domain, self.address()["claimed"]), ([], None, False))
        self.suite.in_use = set()
        self.assertEqual(self.claim()[0], 200)
        self.assertEqual(self.suite.claims, ["999001"])

    def test_an_outage_or_the_claim_limits_say_so_in_spanish_with_retry_and_change_nothing(self):
        self.choose()
        self.suite.down = {"claim", "address"}
        status, body = self.claim()
        self.assertEqual(status, 503)
        self.assertIn("servicio de nombres", body["error"])
        self.assertIn("Reintentar", body["error"])
        self.suite.down = set()
        self.suite.limited = True
        status, body = self.claim()
        self.assertEqual(status, 503)
        self.assertIn("limita", body["error"])
        self.assertIn("Reintentar", body["error"])
        self.assertEqual((self.suite.claims, self.suite.domain, self.address()["claimed"]), ([], None, False))

    def test_a_claim_that_succeeded_is_reused_not_repeated(self):
        self.choose()
        self.suite.down = {"address"}
        status, body = self.claim()
        self.assertEqual(status, 503)
        self.assertIn("Reintentar", body["error"])
        self.assertEqual((self.suite.claims, self.suite.published, self.address()["claimed"]), (["999001"], [], False))
        port = self.server.server_address[1]
        self.stop()
        self.start(port)
        self.suite.down = set()
        self.assertEqual(self.claim()[1]["address"]["claimed"], True)
        self.assertEqual(self.claim()[1]["address"]["claimed"], True)
        self.assertEqual((self.suite.claims, self.suite.domain), (["999001"], NAME))
        self.assertEqual(self.suite.published, [provisionador.lan_ip()])

    def test_once_claimed_the_centre_keeps_its_name_and_only_its_point_can_move(self):
        self.choose()
        self.claim()
        status, body = self.choose("999002")
        self.assertEqual(status, 409)
        self.assertIn(NAME, body["error"])
        moved = {"latitude": -33.4512, "longitude": -70.6634}
        self.assertEqual(self.choose(point=moved)[0], 200)
        state = self.browser.get("/api/state")[1]
        self.assertEqual((state["centre"]["code"], state["centre"]["point"], state["address"]["name"]),
                         ("999001", moved, NAME))

    def test_a_stored_claim_locks_the_centre_and_names_the_suite_even_before_the_wizard_records_it(self):
        self.choose()
        self.suite.down = {"address"}
        self.assertEqual(self.claim()[0], 503)
        status, body = self.choose("999002")
        self.assertEqual(status, 409)
        self.assertIn(NAME, body["error"])
        self.suite.down = set()
        status, body = self.claim()
        self.assertEqual((status, body["address"]["name"], self.suite.domain), (200, NAME, NAME))


COMPONENTS = {"talk", "eurooffice", "imaginary", "clamav", "fulltextsearch"}  # HaRP stays off: no app is an ExApp
# This ticket's steps, by what they need of the suite: Nextcloud's command line, or AIO and the name service alone.
# Later tickets add steps of their own; these tests go by these.
NEEDS_NEXTCLOUD = {"Certificado del ISP", "Idioma y formato regional", "Sesiones y registro de auditoría",
                   "Tareas en segundo plano", "Zona horaria", "Tema y marca"}
AIO_SIDE = {"Componentes", "Copias de seguridad", "Inicio de la suite", "Dirección de la suite",
            "Archivo de contraseñas"}
SECURITY = "Sesiones y registro de auditoría"
MENU = "Menú personalizado"
THEME = "Tema y marca"


def openssl(*args, data=None):
    return subprocess.run(["openssl", *map(str, args)], input=data, check=True, capture_output=True).stdout


class AppKey:
    """A throwaway app key, which signs the tests' App releases as signify signs APS Conecta's: Ed25519 over
    SHA256SUMS, with the key's number before the key and before each signature."""

    def __init__(self):
        self.secret = openssl("genpkey", "-algorithm", "ed25519")
        self.number = os.urandom(8)
        self.public = self.armoured(openssl("pkey", "-pubout", "-outform", "DER", data=self.secret)[-32:])

    def armoured(self, raw):
        return f"untrusted comment: clave de prueba\n{base64.b64encode(b'Ed' + self.number + raw).decode()}\n"

    def sign(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            message = Path(tmp, "message")
            message.write_bytes(data)  # Ed25519 signs the whole message at once, so openssl needs its size
            return self.armoured(openssl("pkeyutl", "-sign", "-rawin", "-inkey", "/dev/stdin", "-in", message,
                                         data=self.secret)).encode()


KEY = AppKey()
RELEASED = [app for app in apps.MANIFEST if app["source"] != "store"]  # the own and fork apps
DEFAULT_APPS = {app["id"] for app in apps.MANIFEST if not app.get("optional")}
OPTIONAL_APPS = {app["id"] for app in apps.MANIFEST if app.get("optional")}
APPS_STEP = "Aplicaciones"
versions = itertools.count(1)


def app_release(app, nextcloud=FakeSuite.nextcloud, key=KEY, extra=()):
    """An App release of the app: its tag, and its four assets, for that Nextcloud version (both bounds) and signed with
    the key. Its tarball holds the app's info.xml, and the `extra` files."""
    version = f"1.{next(versions)}.0"
    info = (f'<?xml version="1.0"?>\n<info><id>{app}</id><version>{version}</version><dependencies>'
            f'<nextcloud min-version="{nextcloud}" max-version="{nextcloud}"/></dependencies></info>\n').encode()
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as tar:
        for name in (f"{app}/appinfo/info.xml", *extra):
            member = tarfile.TarInfo(name)
            member.size = len(info)
            tar.addfile(member, io.BytesIO(info))
    assets = {f"{app}.tar.gz": packed.getvalue(), "info.xml": info}
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in assets.items()).encode()
    return f"v{version}", {**assets, "SHA256SUMS": sums, "SHA256SUMS.minisig": key.sign(sums)}


PUBLISHED = {app["repo"]: dict([app_release(app["id"])]) for app in RELEASED}


def publish(suite):
    """GitHub serves one compatible App release of each own and fork app, signed with the test app key, which the
    suite ships; the open app store offers every store app."""
    suite.app_key = KEY.public
    suite.app_releases = {repo: dict(releases) for repo, releases in PUBLISHED.items()}
    suite.store = {app["id"] for app in apps.MANIFEST if app["source"] == "store"}


class InstallTest(WizardTest):
    """A wizard whose operator has a session, with what «Revisar» and «Instalar» need."""

    def setUp(self):
        super().setUp()
        journal = mock.patch("sys.stdout", io.StringIO())  # the Provisionador's journal, out of the tests' output
        self.journal = journal.start()
        self.addCleanup(journal.stop)
        self.addCleanup(self.settle)
        self.browser = self.operator()
        publish(self.suite)

    def settle(self):
        """Let an install still running finish before its state directory goes."""
        self.suite.release.set()
        self.suite.answering, self.suite.down = True, set()
        if self.server and self.server.app.worker:
            self.server.app.worker.join(10)

    def claim(self, code="999001"):
        """The centre and its address, with the people spreadsheet «Instalar» needs."""
        self.browser.request("POST", "/api/centre", {"code": code})
        self.assertEqual(self.browser.request("POST", "/api/address", {})[0], 200)
        self.browser.request("POST", "/api/people", {"spreadsheet": spreadsheet("ana;Ana;Rojas;;;sí")})

    def install(self, **body):
        return self.browser.request("POST", "/api/install", body)

    def progress(self):
        return self.browser.get("/api/state")[1]["install"]

    def until(self, status):
        """The install's progress once it reaches `status`."""
        deadline = time.monotonic() + 10
        while (install := self.progress())["status"] != status:
            self.assertLess(time.monotonic(), deadline, install)
            time.sleep(0.02)
        return install

    def until_step(self, name, status):
        deadline = time.monotonic() + 10
        while self.steps(install := self.progress())[name][0] != status:
            self.assertLess(time.monotonic(), deadline, install)
            time.sleep(0.02)
        return install

    def until_checked(self, name):
        """The install's progress once the end-of-install check reached the step: it runs, every other one is green."""
        deadline = time.monotonic() + 10
        while True:
            install = self.progress()
            if {n: s for n, (s, _) in self.steps(install).items() if s != "green"} == {name: "running"}:
                return install
            self.assertLess(time.monotonic(), deadline, install)
            time.sleep(0.02)

    @staticmethod
    def steps(install):
        """Each step's status and reason, by its Spanish name."""
        return {s["name"]: (s["status"], s.get("reason")) for s in install["steps"]}

    def named(self, install, status):
        """The steps with that status."""
        return {name for name, (now, _) in self.steps(install).items() if now == status}

    def member_of(self, user):
        return {gid for gid, group in self.suite.groups.items() if user in group["members"]}

    @staticmethod
    def texts(install):
        """The install's log, by its lines' texts."""
        return [line["text"] for line in install["log"]]

    def until_logged(self, text):
        deadline = time.monotonic() + 10
        while text not in self.texts(install := self.progress()):
            self.assertLess(time.monotonic(), deadline, install["log"])
            time.sleep(0.02)
        return install


class Instalar(InstallTest):
    """«Revisar» and «Instalar»: one summary, then the install's steps, each once the steps it needs are green; the
    progress shows them and, once AIO runs only our components, each container as AIO reports it. «Listo» needs every
    step green and a clean end-of-install check."""

    def test_revisar_summarises_the_centre_the_address_the_components_and_the_backups(self):
        self.claim()
        state = self.browser.get("/api/state")[1]
        self.assertEqual((state["centre"]["name"], state["address"]["name"], state["install"]),
                         ("Centro de Salud Familiar Los Castaños", NAME, None))
        self.assertEqual(state["components"], [
            "Talk", "Euro-Office", "Imaginary", "ClamAV (antivirus)", "Búsqueda de texto completo"])
        self.assertEqual(state["backup_folder"], "/var/backups/aps-conecta")

    def test_there_is_no_install_before_the_address_is_claimed(self):
        self.browser.request("POST", "/api/centre", {"code": "999001"})
        status, body = self.install()
        self.assertEqual(status, 409)
        self.assertIn("«Dirección»", body["error"])
        self.assertEqual((self.suite.switched, self.suite.backup, self.suite.starts), (None, None, 0))

    def test_instalar_runs_every_step_and_listo_follows_a_clean_end_of_install_check(self):
        self.claim("999002")
        status, body = self.install()
        self.assertEqual((status, body["install"]["status"]), (202, "installing"))
        install = self.until("done")
        self.assertLessEqual(NEEDS_NEXTCLOUD | AIO_SIDE, set(self.steps(install)))
        self.assertEqual(self.named(install, "green"), set(self.steps(install)), "every step the API lists is green")
        self.assertEqual(self.suite.switched, COMPONENTS)
        self.assertEqual(self.suite.backup, ("/var/backups/aps-conecta", "06:00"))
        self.assertIs(self.suite.automatic_updates, True)  # AIO's nightly job updates the store's apps
        self.assertEqual(self.suite.starts, 1)
        self.assertLessEqual({
            "default_language": "es", "force_language": "es", "default_locale": "es_CL", "default_phone_region": "CL",
            "default_timezone": "America/Punta_Arenas", "logtimezone": "America/Punta_Arenas",
            "remember_login_cookie_lifetime": 0, "session_lifetime": 900, "session_keepalive": False,
            "maintenance_window_start": 1}.items(), self.suite.system.items())
        self.assertEqual((self.suite.apps["admin_audit"]["enabled"], self.suite.apps["core"]["backgroundjobs_mode"]),
                         ("yes", "cron"))
        self.assertEqual(self.suite.issuers, ["www.ispch.gob.cl"])

    def test_progress_shows_each_container_as_aio_reports_it(self):
        """A component's container goes by its «Revisar» name; every other one keeps AIO's."""
        self.claim()
        self.suite.release.clear()
        self.suite.states = {"nextcloud-aio-database": ("PostgreSQL", "running"),
                             "nextcloud-aio-nextcloud": ("Servidor de aplicaciones", "starting"),
                             "nextcloud-aio-fulltextsearch": ("Fulltextsearch", "stopped")}
        self.install()
        install = self.until_step("Inicio de la suite", "running")
        names = ["PostgreSQL", "Servidor de aplicaciones", "Búsqueda de texto completo"]
        self.assertEqual((install["status"], install["containers"]), ("installing", [
            {"name": name, "state": state} for name, state in zip(names, ("running", "starting", "stopped"))]))
        self.suite.states["nextcloud-aio-fulltextsearch"] = ("Fulltextsearch", "starting")
        self.assertEqual(self.progress()["containers"][2], {"name": "Búsqueda de texto completo", "state": "starting"})
        self.suite.release.set()
        self.assertEqual(self.until("done")["containers"], [{"name": name, "state": "running"} for name in names])

    def test_container_progress_shows_only_once_aio_runs_our_components(self):
        """Before the components step, AIO's own list still holds what it runs by default, such as Collabora."""
        self.claim()
        self.suite.states = {"nextcloud-aio-collabora": ("Collabora", "stopped")}
        self.suite.down = {"aio"}
        self.install()
        install = self.until("failed")
        self.assertEqual(self.steps(install)["Componentes"][0], "failed")
        self.assertNotIn("containers", install)
        self.suite.down = set()
        self.suite.states = {"nextcloud-aio-talk": ("Talk", "stopped")}
        self.install()
        self.assertEqual(self.until("done")["containers"], [{"name": "Talk", "state": "running"}])

    def test_a_reading_of_the_containers_that_fails_says_why_and_the_progress_goes_on(self):
        self.claim()
        self.suite.release.clear()
        self.suite.states = {"nextcloud-aio-talk": ("Talk", "starting")}
        self.install()
        self.until_step("Inicio de la suite", "running")
        self.suite.down = {"aio"}
        install = self.progress()
        self.assertEqual((install["status"], install["containers"]), ("installing", []))
        self.assertIn("contenedor maestro", install["error"])
        self.suite.down = set()
        install = self.progress()
        self.assertEqual((install["containers"], "error" in install), ([{"name": "Talk", "state": "starting"}], False))

    def test_instalar_again_while_installing_or_once_listo_starts_nothing_more(self):
        self.claim()
        self.suite.release.clear()
        self.install()
        self.assertEqual(self.install()[1]["install"]["status"], "installing")
        self.suite.release.set()
        self.until("done")
        commands = len(self.suite.commands)
        self.assertEqual(self.install()[1]["install"]["status"], "done")
        self.assertEqual((self.suite.starts, len(self.suite.commands)), (1, commands))

    def test_a_poll_reads_whether_the_install_runs_together_with_its_steps(self):
        """The poll's status matches the steps it read, even if «Reintentar» starts a run meanwhile. The fake suite port
        cannot reach the moment between the install's last green step and «Listo», so snapshot() keeps the order that
        covers it: whether the install runs, then the state."""
        self.claim()
        self.suite.down = {"aio"}
        self.install()
        self.until("failed")
        self.suite.down = set()
        self.suite.release.clear()
        held, go, answer = threading.Event(), threading.Event(), {}

        def hardware():  # the poll's reading of the server, held until `go`; the install's own reading is not
            del self.suite.hardware
            held.set()
            go.wait(10)
            return self.suite.hardware()

        self.suite.hardware = hardware
        poll = threading.Thread(target=lambda: answer.update(self.progress()))
        poll.start()
        self.assertTrue(held.wait(10))
        self.install()
        go.set()
        poll.join(10)
        self.assertEqual((answer["status"], self.steps(answer)["Componentes"][0]), ("failed", "failed"))
        self.assertEqual(self.progress()["status"], "installing")


class StepRunner(InstallTest):
    """The step runner: a failed step never stops the steps that do not need it, its dependents wait, each red step
    says why, and «Reintentar» reruns only those."""

    def test_an_aio_outage_fails_the_steps_that_need_aio_and_their_dependents_wait_with_a_reason(self):
        self.claim()
        self.suite.down = {"aio"}
        self.install()
        steps = self.steps(self.until("failed"))
        for name in ("Componentes", "Copias de seguridad"):
            self.assertEqual(steps[name][0], "failed")
            self.assertIn("contenedor maestro", steps[name][1])
            self.assertIn("Reintentar", steps[name][1])
        self.assertEqual(steps["Inicio de la suite"],
                         ("waiting", "Espera a que se complete: «Componentes», «Copias de seguridad»."))
        self.assertEqual(steps["Dirección de la suite"], ("green", None), "it needs no other step")
        for name in NEEDS_NEXTCLOUD | {"Archivo de contraseñas"}:
            self.assertEqual(steps[name], ("waiting", "Espera a que se complete: «Inicio de la suite»."), name)
        self.assertEqual((self.suite.starts, self.suite.commands), (0, []))
        self.suite.down = set()
        self.assertEqual(self.install()[0], 202)
        self.until("done")
        self.assertEqual((self.suite.switched, self.suite.starts), (COMPONENTS, 1))

    def test_a_missing_app_fails_its_step_loudly_and_every_step_that_does_not_need_it_goes_on(self):
        self.claim()
        del self.suite.apps["admin_audit"]
        self.install()
        install = self.until("failed")
        status, reason = self.steps(install)[SECURITY]
        self.assertEqual(status, "failed")
        self.assertIn("occ app:enable admin_audit", reason)
        self.assertLessEqual((NEEDS_NEXTCLOUD - {SECURITY}) | AIO_SIDE, self.named(install, "green"))
        self.assertNotIn("session_lifetime", self.suite.system, "no setting of a failed step is reported as made")

    def test_reintentar_reruns_only_the_failed_and_waiting_steps(self):
        self.claim()
        del self.suite.apps["admin_audit"]
        self.install()
        self.until("failed")
        imports, starts = len(self.suite.imports), self.suite.starts
        self.suite.apps["admin_audit"] = {}
        self.install()
        self.assertEqual(self.steps(self.until("done"))[SECURITY], ("green", None))
        imported = {key for settings in self.suite.imports[imports:] for key in settings.get("system", {})}
        self.assertIn("session_lifetime", imported)
        self.assertFalse(imported & {"default_language", "default_timezone", "maintenance_window_start"},
                         "a green step does not run again")
        self.assertEqual((self.suite.starts, self.suite.system["session_lifetime"]), (starts, 900))

    def test_a_nextcloud_that_does_not_answer_fails_every_step_that_needs_it(self):
        self.claim()
        self.suite.down = {"nextcloud"}
        self.install()
        install = self.until("failed")
        self.assertLessEqual(NEEDS_NEXTCLOUD, self.named(install, "failed"))
        for name in NEEDS_NEXTCLOUD:
            self.assertIn("contenedor de Nextcloud no responde", self.steps(install)[name][1])
        self.assertLessEqual(AIO_SIDE, self.named(install, "green"))
        self.assertEqual((self.suite.system, self.suite.issuers), ({}, []))

    def test_listo_waits_until_the_suite_answers_at_its_name_once_its_inside_address_is_published_again(self):
        self.claim()
        self.suite.answering = False
        self.install()
        self.assertEqual(self.until_checked("Dirección de la suite")["status"], "installing")
        self.suite.answering = True
        self.until("done")
        self.assertEqual(self.suite.published, [provisionador.lan_ip()] * 2)

    def test_the_end_of_install_check_finds_what_changed_after_its_step_and_reintentar_sets_it_again(self):
        """A setting that changed once its step went green keeps «Listo» away until «Reintentar» reruns that step."""
        self.claim()
        self.suite.down = {"issuer"}
        self.install()
        steps = self.steps(self.until("failed"))
        self.assertEqual(steps["Certificado del ISP"][0], "failed")
        self.assertIn("www.ispch.gob.cl", steps["Certificado del ISP"][1])
        self.assertEqual(steps[SECURITY][0], "green")
        self.suite.system["session_lifetime"] = 86400
        self.suite.down = set()
        self.install()
        install = self.until("failed")
        status, reason = self.steps(install)[SECURITY]
        self.assertEqual(status, "failed")
        self.assertIn("session_lifetime", reason)
        self.assertLessEqual((NEEDS_NEXTCLOUD - {SECURITY}) | AIO_SIDE, self.named(install, "green"))
        self.install()
        self.until("done")
        self.assertEqual((self.suite.system["session_lifetime"], self.suite.issuers), (900, ["www.ispch.gob.cl"]))

    def test_the_end_of_install_check_reads_the_settings_config_list_hides(self):
        """occ config:list hides theming's link as sensitive: the check reads it, so a changed one is found too."""
        self.claim()
        self.suite.down = {"issuer"}
        self.install()
        self.assertEqual(self.steps(self.until("failed"))[THEME], ("green", None))
        self.suite.apps["theming"]["url"] = "https://nextcloud.com"
        self.suite.down = set()
        self.install()
        status, reason = self.steps(self.until("failed"))[THEME]
        self.assertEqual(status, "failed")
        self.assertIn("theming.url", reason)
        self.install()
        self.until("done")
        self.assertEqual(self.suite.apps["theming"]["url"], "https://apsconecta.cl")

    def test_the_end_of_install_check_flags_a_slogan_that_is_not_the_motto_and_reintentar_sets_it_again(self):
        self.claim()
        self.suite.down = {"issuer"}
        self.install()
        self.assertEqual(self.steps(self.until("failed"))[THEME], ("green", None))
        self.suite.apps["theming"]["slogan"] = "un hogar seguro para todos tus datos"
        self.suite.down = set()
        self.install()
        status, reason = self.steps(self.until("failed"))[THEME]
        self.assertEqual(status, "failed")
        self.assertIn("theming.slogan", reason)
        self.install()
        self.until("done")
        self.assertEqual(self.suite.apps["theming"]["slogan"], MOTTO)

    def help_link_flagged_then_set_again(self, change):
        """The theme step green while the install failed elsewhere, `change` to Nextcloud's system settings: the
        end-of-install check flags the help link, and «Reintentar» sets it again."""
        self.claim("999004")
        self.suite.down = {"issuer"}
        self.install()
        self.assertEqual(self.steps(self.until("failed"))[THEME], ("green", None))
        change(self.suite.system)
        self.suite.down = set()
        self.install()
        status, reason = self.steps(self.until("failed"))[THEME]
        self.assertEqual(status, "failed")
        self.assertIn("lost_password_link", reason)
        self.install()
        self.until("done")
        self.assertEqual(self.suite.system["lost_password_link"], HELP_LINK)

    def test_the_end_of_install_check_flags_a_missing_help_link_and_reintentar_sets_it_again(self):
        self.help_link_flagged_then_set_again(lambda system: system.pop("lost_password_link", None))

    def test_the_end_of_install_check_flags_a_changed_help_link_and_reintentar_sets_it_again(self):
        self.help_link_flagged_then_set_again(lambda system: system.update(lost_password_link="https://nextcloud.com"))


class Upgrade(InstallTest):
    """The one command's upgrade of an installed server: `provisionador.py upgrade` (install.sh runs it once the new
    release's bundle and mastercontainer are in place) stops the suite, so AIO's forms can be posted again, and sets
    every step but the people's to run again; the restarted Provisionador resumes the install by itself and «Listo»
    follows, as at the first install. The people stay with their Users page."""

    def upgrade(self, version="v1.1.0"):
        return provisionador.upgrade(self.suite, provisionador.State(self.state), version)

    def installed(self):
        """A finished, closed install, as the server holds it once the wizard ended: the Provisionador is stopped."""
        self.claim()
        self.assertEqual(self.install()[0], 202)
        self.until("done")
        self.assertEqual(self.browser.request("POST", "/api/close", {})[0], 200)
        self.stop()

    def test_an_install_still_under_way_is_left_to_resume_not_reconciled(self):
        self.claim()
        self.suite.release.clear()  # the install never reaches «Listo»
        self.install()
        self.until_step(start.NAME, "running")
        self.suite.release.set()
        self.assertEqual(self.upgrade(), None)
        self.assertEqual(self.suite.stops, 0)  # nothing stops: the Provisionador carries the install on
        install = provisionador.State(self.state).read()["install"]
        self.assertNotIn("upgrade", install)  # and no reconcile was marked

    def test_the_upgrade_stops_the_suite_and_sets_every_step_but_the_people_to_run_again(self):
        self.installed()
        before = len(self.suite.commands)
        self.assertEqual(self.upgrade(), None)
        self.assertEqual(self.suite.stops, 1)
        state = provisionador.State(self.state).read()
        owned = {name for name, module in runner.STEPS.items() if getattr(module, "OWNED", False)}
        self.assertEqual(owned, {"people"})
        self.assertEqual({name: step["status"] for name, step in state["install"]["steps"].items()},
                         {name: "green" if name in owned else "pending" for name in runner.STEPS})
        self.assertEqual((state["install"].get("listo"), state.get("closed")), (False, None))
        self.start()  # the bootstrap restarts the Provisionador; it resumes the install by itself
        self.browser = self.operator()
        install = self.until("done")
        self.assertIn("Comienza la actualización a v1.1.0.", self.texts(install))
        self.assertEqual(self.steps(install)[people.NAME], ("green", None))
        self.assertEqual(self.suite.starts, 2)
        self.assertIs(self.suite.automatic_updates, True)  # the release's backups step ran again
        self.assertFalse([c for c in self.suite.commands[before:] if c[0].startswith("user:")],
                         "the upgrade touched the people's accounts")
        self.assertEqual(self.browser.request("POST", "/api/close", {})[0], 200)  # the wizard closes again

    def test_an_upgrade_keeps_the_people_with_their_users_page(self):
        self.installed()
        self.suite.users["ana"]["enabled"] = False  # an administrator disabled a person after the install
        self.assertEqual(self.upgrade(), None)
        self.start()
        self.browser = self.operator()
        self.assertEqual(self.until("done")["status"], "done")
        self.assertFalse(self.suite.users["ana"]["enabled"], "the upgrade re-enabled a person the Users page disabled")


class StoreApps(unittest.TestCase):
    """The nightly update of the store's apps (the host's timer runs `provisionador.py update-apps` after AIO's backup
    window): it waits out the suite being down, as it is while AIO's nightly job restarts its containers, and runs
    Nextcloud's own update of the store's apps. Own and fork apps stay on the release's manifest."""

    def test_the_stores_apps_update_runs_nextclouds_own(self):
        suite = FakeSuite()
        self.assertEqual(provisionador.update_apps(suite, wait=0), 0)
        self.assertEqual(suite.app_updates, 1)

    def test_it_waits_out_the_backup_window_and_leaves_a_note_when_the_suite_never_answers(self):
        suite = FakeSuite()
        suite.down = {"nextcloud"}
        with mock.patch("provisionador.time.sleep") as sleep:
            self.assertEqual(provisionador.update_apps(suite, wait=0.2, retry=5), 1)
        self.assertGreaterEqual(sleep.call_count, 2, "it did not wait for the suite to answer")


class Apps(InstallTest):
    """The Suite manifest's apps: every default app and the optional apps ticked on «Revisar». An own or fork app comes
    from its newest App release compatible with the running Nextcloud, verified against the app key, and the lock file
    records its tag; a store app comes from the open app store. One app that fails is named in red while every other
    app and step goes on, and «Reintentar» installs it."""

    def enabled(self):
        return {app for app, values in self.suite.apps.items() if values.get("enabled") == "yes"}

    def lock(self):
        return json.loads((self.state / "apps.lock.json").read_text())

    def newest(self, app):
        return next(iter(self.suite.app_releases[app["repo"]]))

    def release_newer(self, app, **release):
        """GitHub serves a newer App release of the app, before the ones it served."""
        repo = app["repo"]
        self.suite.app_releases[repo] = dict([app_release(app["id"], **release), *self.suite.app_releases[repo].items()])

    def context(self):
        """What a step that configures an app gets, as the Provisionador's install gives it."""
        return SimpleNamespace(suite=self.suite, folder=self.state, optional=frozenset())

    def test_revisar_lists_the_default_apps_and_offers_the_optional_ones_off(self):
        self.claim()
        state = self.browser.get("/api/state")[1]
        self.assertEqual(state["apps"], [app["name"] for app in apps.MANIFEST if not app.get("optional")])
        self.assertEqual([(a["name"], a["chosen"]) for a in state["optional_apps"]],
                         [("Farmacia", False), ("Estadística", False), ("Tótem", False)])
        self.assertTrue(all(a["summary"] for a in state["optional_apps"]))

    def test_instalar_enables_every_default_app_and_records_each_own_and_fork_apps_tag(self):
        self.claim()
        self.install()
        self.until("done")
        self.assertLessEqual(DEFAULT_APPS, self.enabled())
        self.assertFalse(OPTIONAL_APPS & self.enabled(), "no optional app is installed unless ticked")
        self.assertEqual(self.suite.api_calls, 0, "the latest releases are compatible: GitHub's API is not called")
        defaults = [app for app in RELEASED if not app.get("optional")]
        self.assertEqual(self.lock(), {app["id"]: self.newest(app) for app in defaults})
        self.assertEqual(self.suite.unpacked, [
            (app["id"], self.suite.app_releases[app["repo"]][self.newest(app)][f"{app['id']}.tar.gz"])
            for app in defaults])

    def test_only_the_ticked_optional_apps_install_and_the_choice_stays(self):
        self.claim()
        self.assertEqual(self.install(optional=["farmacia"])[0], 202)
        self.until("done")
        self.assertIn("farmacia", self.enabled())
        unpacked = {app for app, _ in self.suite.unpacked}
        self.assertFalse({"estadistica", "totem"} & (unpacked | self.enabled() | set(self.lock())))
        self.assertIn("farmacia", self.lock())
        chosen = {a["id"]: a["chosen"] for a in self.browser.get("/api/state")[1]["optional_apps"]}
        self.assertEqual(chosen, {"farmacia": True, "estadistica": False, "totem": False})

    def test_an_optional_app_the_manifest_does_not_offer_is_refused(self):
        self.claim()
        for optional in (["intravox"], ["desconocida"], "farmacia", [["farmacia"]]):
            status, body = self.install(optional=optional)
            self.assertEqual(status, 400, optional)
            self.assertIn("aplicaciones opcionales", body["error"])
        self.assertEqual((self.progress(), self.suite.starts), (None, 0))

    def test_the_newest_release_compatible_with_the_running_nextcloud_installs(self):
        app = RELEASED[0]
        compatible = self.newest(app)
        self.release_newer(app, nextcloud=FakeSuite.nextcloud + 1)
        self.claim()
        self.install()
        self.until("done")
        self.assertEqual(self.lock()[app["id"]], compatible)
        self.assertEqual(self.suite.api_calls, 1, "only the app whose latest release is incompatible asks the API")

    def test_a_release_whose_bounds_name_a_minor_version_admits_that_major(self):
        app = RELEASED[0]
        tag, assets = app_release(app["id"], nextcloud=f"{FakeSuite.nextcloud}.0.1")
        self.suite.app_releases[app["repo"]] = {tag: assets}
        self.claim()
        self.install()
        self.until("done")
        self.assertEqual(self.lock()[app["id"]], tag)

    def test_a_package_with_more_than_the_app_or_an_old_release_under_a_new_tag_is_refused_with_its_reason(self):
        outside, republished = [app for app in RELEASED if not app.get("optional")][:2]
        self.suite.app_releases[outside["repo"]] = dict([app_release(outside["id"], extra=["otra/appinfo/info.xml"])])
        old = next(iter(self.suite.app_releases[republished["repo"]].values()))
        self.suite.app_releases[republished["repo"]] = {app_release(republished["id"])[0]: old}
        self.claim()
        self.install()
        reason = self.steps(self.until("failed"))[APPS_STEP][1]
        for app in (outside, republished):
            self.assertRegex(reason, f"«{app['name']}»: [^«]*no contiene solo la aplicación en esa versión")
        self.assertFalse({outside["id"], republished["id"]} & {app for app, _ in self.suite.unpacked})

    def test_a_bad_signature_or_checksum_refuses_that_app_with_its_reason_and_reintentar_installs_it(self):
        # Apps no other step configures: the welcome page ensures IntraVox, and would fail with it.
        signed, tampered = [app for app in RELEASED if not app.get("optional") and app["id"] != "intravox"][:2]
        good = {app["repo"]: dict(self.suite.app_releases[app["repo"]]) for app in (signed, tampered)}
        tag, assets = next(iter(good[signed["repo"]].items()))
        forger = AppKey()
        forger.number = KEY.number  # a signature that names the app key, made with another one
        forged = forger.sign(assets["SHA256SUMS"])
        self.suite.app_releases[signed["repo"]] = {tag: {**assets, "SHA256SUMS.minisig": forged}}
        tag, assets = next(iter(good[tampered["repo"]].items()))
        package = f"{tampered['id']}.tar.gz"
        self.suite.app_releases[tampered["repo"]] = {tag: {**assets, package: assets[package] + b"\0"}}
        self.claim()
        self.install()
        install = self.until("failed")
        status, reason = self.steps(install)[APPS_STEP]
        self.assertEqual(status, "failed")
        self.assertRegex(reason, f"«{signed['name']}»: [^«]*clave de aplicaciones de APS Conecta")
        self.assertRegex(reason, f"«{tampered['name']}»: [^«]*suma de verificación")
        unpacked = [app for app, _ in self.suite.unpacked]
        self.assertFalse({signed["id"], tampered["id"]} & (set(unpacked) | set(self.lock())))
        self.assertLessEqual(DEFAULT_APPS - {signed["id"], tampered["id"]}, self.enabled())
        self.assertEqual(self.named(install, "failed"), {APPS_STEP}, "every other step goes on")
        self.suite.app_releases.update(good)
        self.install()
        self.until("done")
        self.assertLessEqual(DEFAULT_APPS, self.enabled())
        self.assertEqual([app for app, _ in self.suite.unpacked[len(unpacked):]], [signed["id"], tampered["id"]],
                         "«Reintentar» installs only the apps that failed")
        self.assertLessEqual({signed["id"], tampered["id"]}, set(self.lock()))

    def test_a_failed_download_or_a_store_app_that_does_not_install_names_that_app_and_reintentar_installs_it(self):
        app, store = RELEASED[0], next(a for a in apps.MANIFEST if a["source"] == "store")
        releases = self.suite.app_releases[app["repo"]]
        tag = next(iter(releases))
        releases[tag] = {name: data for name, data in releases[tag].items() if name != f"{app['id']}.tar.gz"}
        self.suite.store.remove(store["id"])
        self.claim()
        self.install()
        reason = self.steps(self.until("failed"))[APPS_STEP][1]
        self.assertIn(f"«{app['name']}»: No se pudo descargar {app['id']}.tar.gz", reason)
        self.assertIn(f"«{store['name']}»: Nextcloud no pudo completar «occ app:enable {store['id']}»", reason)
        self.assertLessEqual(DEFAULT_APPS - {app["id"], store["id"]}, self.enabled())
        publish(self.suite)
        self.install()
        self.until("done")
        self.assertLessEqual(DEFAULT_APPS, self.enabled())

    def test_an_app_that_installs_on_reintentar_gets_its_browser_texts_then(self):
        """The browser texts are built again once «Reintentar» installs an app the theme step, already green, found
        missing, rather than at the next start of Nextcloud's container."""
        self.suite.store.remove("calendar")
        self.claim()
        self.install()
        steps = self.steps(self.until("failed"))
        self.assertEqual((steps[THEME][0], steps[APPS_STEP][0]), ("green", "failed"))
        publish(self.suite)
        self.install()
        self.until("done")
        built = self.suite.texts_built[-1]
        self.assertIn("calendar", built["apps"])
        self.assertIn("apsconecta", built["themes"])

    def test_a_step_that_configures_an_app_ensures_it_and_ensuring_it_again_installs_nothing(self):
        app = RELEASED[0]
        self.assertEqual(apps.ensure(self.context(), app["id"]), [])
        self.assertIn(app["id"], self.enabled())
        self.assertEqual(self.lock(), {app["id"]: self.newest(app)})
        unpacked, commands = len(self.suite.unpacked), len(self.suite.commands)
        self.assertEqual(apps.ensure(self.context(), app["id"]), [])
        self.assertEqual(len(self.suite.unpacked), unpacked)
        self.assertNotIn("app:enable", {command[0] for command in self.suite.commands[commands:]})

    def test_ensure_names_only_its_own_apps_failure_while_the_apps_step_names_every_failure(self):
        ensured, other = [app for app in RELEASED if not app.get("optional")][:2]
        for app in (ensured, other):
            tag, assets = next(iter(self.suite.app_releases[app["repo"]].items()))
            package = f"{app['id']}.tar.gz"
            self.suite.app_releases[app["repo"]] = {tag: {**assets, package: assets[package] + b"\0"}}
        reasons = apps.ensure(self.context(), ensured["id"])
        self.assertEqual(len(reasons), 1)
        self.assertRegex(reasons[0], f"^«{ensured['name']}»: [^«]*suma de verificación")
        self.claim()
        self.install()
        reason = self.steps(self.until("failed"))[APPS_STEP][1]
        for app in (ensured, other):
            self.assertIn(f"«{app['name']}»: ", reason)

    def test_the_end_of_install_check_finds_a_disabled_app_and_reintentar_enables_its_locked_release_again(self):
        app = RELEASED[0]
        self.claim()
        self.suite.answering = False
        self.install()
        self.until_checked("Dirección de la suite")
        locked = self.lock()[app["id"]]
        self.suite.apps[app["id"]]["enabled"] = "no"
        self.release_newer(app)
        self.suite.answering = True
        status, reason = self.steps(self.until("failed"))[APPS_STEP]
        self.assertEqual(status, "failed")
        self.assertIn(app["name"], reason)
        self.install()
        self.until("done")
        self.assertEqual(self.lock()[app["id"]], locked, "a retry installs the tag the lock file records")
        self.assertIn(app["id"], self.enabled())


ESTABLISHMENT_STEP = "Establecimiento en Estadística"


class EstadisticaEstablishment(InstallTest):
    """Estadística reads the centre from its app settings: the DEIS code, the register's type and the comuna's CUT."""

    REGISTER = REGISTER + "99905,CESFAM,Centro de Salud Familiar Código Corto,Calle Cinco 500,13101,Santiago,13," \
                          "Metropolitana de Santiago,Servicio de Salud Metropolitano Central,Municipal,-33.46,-70.65\n"

    def held(self):
        return {key: json.loads(self.suite.occ("config:app:get", "estadistica", key, "--output=json"))
                for key in ("deis_code", "establishment_type", "comuna_cut")}

    def test_instalar_with_estadistica_gives_it_the_centres_establishment(self):
        self.claim("999002")
        self.install(optional=["estadistica"])
        self.until("done")
        self.assertEqual(self.suite.apps["estadistica"]["enabled"], "yes")
        self.assertEqual(self.held(), {"deis_code": "999002", "establishment_type": "CECOSF", "comuna_cut": "12101"})

    def test_without_estadistica_the_keys_are_set_so_an_estadistica_enabled_later_finds_them(self):
        self.claim("999003")
        self.install()
        self.until("done")
        self.assertNotEqual(self.suite.apps["estadistica"].get("enabled"), "yes")
        self.assertEqual(self.held(), {"deis_code": "999003", "establishment_type": "SAPU", "comuna_cut": "05201"})

    def test_a_code_that_is_not_six_digits_stops_the_step_naming_it(self):
        self.claim("99905")
        self.install(optional=["estadistica"])
        status, reason = self.steps(self.until("failed"))[ESTABLISHMENT_STEP]
        self.assertEqual(status, "failed")
        self.assertIn("«99905», no tiene seis dígitos", reason)
        self.assertNotIn("deis_code", self.suite.apps["estadistica"])

    def test_a_rerun_sets_the_keys_again_only_when_nextcloud_no_longer_holds_them(self):
        def writes():
            return sum("estadistica" in settings.get("apps", {}) for settings in self.suite.imports)

        self.claim()
        self.suite.down = {"issuer"}
        self.install()
        self.assertEqual(self.steps(self.until("failed"))[ESTABLISHMENT_STEP], ("green", None))
        self.install()
        self.until("failed")
        self.assertEqual(writes(), 1, "Reintentar does not rerun a green step")
        self.suite.apps["estadistica"]["deis_code"] = "999004"
        self.suite.down = set()
        self.install()
        status, reason = self.steps(self.until("failed"))[ESTABLISHMENT_STEP]
        self.assertEqual(status, "failed")
        self.assertIn("estadistica.deis_code", reason)
        self.install()
        self.until("done")
        self.assertEqual((self.held()["deis_code"], writes()), ("999001", 2))


TEAMS = {"sectors": ["Norte", "Sector Sur", "sector norte", " "],
         "programs": ["Programa Atención Domiciliaria", "Salud Mental/Adulto"]}
DECLARED = [
    {"id": "sector-norte", "kind": "sector", "name": "Sector Norte", "folder": "Sectores/Sector Norte"},
    {"id": "sector-sur", "kind": "sector", "name": "Sector Sur", "folder": "Sectores/Sector Sur"},
    {"id": "prog-atencion-domiciliaria", "kind": "program", "name": "Programa Atención Domiciliaria",
     "folder": "Programas/Atención Domiciliaria"},
    {"id": "prog-salud-mental-adulto", "kind": "program", "name": "Programa Salud Mental-Adulto",
     "folder": "Programas/Salud Mental-Adulto"}]
CARGO = {"director", "subdirector", "jefe.farmacia", "jefe.some", "jefe.norte", "jefe.sur"}
# The 41 cargos by category, in order, as a SAPU names them: each cargo's group id and name.
CARGOS = {
    "cat-jefaturas": {
        "role-director-cesfam": "Director/a de SAPU",
        "role-subdirector-jefe-tecnico": "Subdirector/a Médico o Jefe Técnico",
        "role-jefe-sector-mais": "Jefe/a de Sector (Gestión MAIS)",
        "role-jefe-some": "Jefe/a de SOME",
        "role-jefe-programa": "Jefe/a de Programa",
        "role-medico-gestor": "Médico/a Gestor/a",
        "role-jefe-apoyo-clinico": "Jefe/a de Apoyo Clínico",
        "role-jefe-servicios-generales": "Jefe/a de Servicios Generales"},
    "cat-clinicos": {
        "role-medico": "Médico General / de Familia",
        "role-dentista": "Cirujano Dentista",
        "role-quimico-farmaceutico": "Químico Farmacéutico (Dir. Técnico Farmacia)",
        "role-enfermeria": "Enfermera/o",
        "role-matroneria": "Matrona/Matrón",
        "role-kinesiologo": "Kinesiólogo/a",
        "role-psicologo": "Psicólogo/a",
        "role-trabajador-social": "Trabajador/a Social",
        "role-nutricionista": "Nutricionista",
        "role-terapeuta-ocupacional": "Terapeuta Ocupacional",
        "role-fonoaudiologo": "Fonoaudiólogo/a",
        "role-tecnologo-medico": "Tecnólogo/a Médico/a",
        "role-educador-parvulos": "Educador/a de Párvulos",
        "role-profesor-educacion-fisica": "Profesor/a de Educación Física",
        "role-gestor-comunitario": "Gestor/a Comunitario/a",
        "role-medico-oftalmologo": "Médico/a Oftalmólogo/a"},
    "cat-tecnicos": {
        "role-tens-procedimientos": "TENS – Procedimientos / Vacunatorio",
        "role-tens-farmacia": "TENS – Farmacia / PNAC",
        "role-tons": "TONS (Técnico en Odontología)",
        "role-podologo": "Podólogo/a",
        "role-tecnico-rehabilitacion": "Técnico/a en Rehabilitación",
        "role-facilitador-intercultural": "Facilitador/a Intercultural",
        "role-tecnico-salud": "Técnico/a de Salud"},
    "cat-administrativos": {
        "role-administrativo-some": "Administrativo SOME",
        "role-oirs": "Encargado/a OIRS",
        "role-estadistica-rem": "Encargado/a de Estadística (REM)",
        "role-conductor": "Conductor (Ambulancia / Traslado)",
        "role-auxiliar-servicio": "Auxiliar de Servicio",
        "role-informatico": "Informático/a (Soporte TI)",
        "role-recursos-humanos": "Recursos Humanos",
        "role-abastecimiento-finanzas": "Abastecimiento y Finanzas",
        "role-secretario-direccion": "Secretario/a de Dirección",
        "role-guardia-seguridad": "Guardia de Seguridad"}}
READ, READ_SHARE, EDIT, MANAGE = 1, 17, 7, 31  # Groupfolders' permission bits
FOLDERS_STEP = "Carpetas de equipo y permisos"
WELCOME_STEP = "Página de bienvenida"


class Equipos(InstallTest):
    """«Equipos»: each sector and program the operator types becomes a team, with its group and its folder, and each
    sector a cargo account; «Revisar» follows them until «Instalar». The install creates the groups, the group folders
    with their permissions, the welcome page every staff member lands on and the cargo accounts, whose first passwords
    the passwords file lists."""

    def teams(self, **body):
        return self.browser.request("POST", "/api/teams", body)

    def folders(self):
        """Each group folder by its mount point: each group's permissions and the files seeded in it."""
        return {f["mount"]: f for f in self.suite.group_folders.values()}

    def test_each_sector_and_program_becomes_one_team_with_its_group_and_folder(self):
        status, body = self.teams(**TEAMS)
        self.assertEqual((status, body["teams"]), (200, DECLARED))
        self.assertEqual(self.browser.get("/api/state")[1]["teams"], DECLARED)

    def test_a_name_that_is_not_one_line_of_text_is_refused_and_changes_nothing(self):
        for body in ({"sectors": "Norte"}, {"programs": [3]}, {"sectors": ["Nor\x00te"]}, {"programs": ["x" * 61]}):
            status, answer = self.teams(**body)
            self.assertEqual(status, 400, body)
            self.assertTrue(answer["error"], body)
        self.assertEqual(self.browser.get("/api/state")[1]["teams"], [])

    def test_revisar_follows_the_teams_and_their_cargo_accounts_until_instalar_then_they_stay(self):
        self.claim()
        self.teams(**TEAMS)
        state = self.browser.get("/api/state")[1]
        self.assertEqual({a["user"] for a in state["cargo_accounts"]}, CARGO)
        self.assertEqual(self.teams(sectors=["Oriente"], programs=[])[0], 200)
        state = self.browser.get("/api/state")[1]
        self.assertEqual([t["name"] for t in state["teams"]], ["Sector Oriente"])
        self.assertEqual({a["user"] for a in state["cargo_accounts"]},
                         CARGO - {"jefe.norte", "jefe.sur"} | {"jefe.oriente"})
        self.install()
        self.until("done")
        status, body = self.teams(**TEAMS)
        self.assertEqual(status, 409)
        self.assertIn("«Instalar»", body["error"])
        self.assertEqual([t["name"] for t in self.browser.get("/api/state")[1]["teams"]], ["Sector Oriente"])
        self.assertIn("sector-oriente", self.suite.groups)

    def test_instalar_creates_the_groups_and_the_group_folders_with_their_permissions(self):
        self.claim()
        self.teams(**TEAMS)
        self.install()
        self.until("done")
        groups = {gid: group["name"] for gid, group in self.suite.groups.items()}
        self.assertLessEqual({"all-staff": "Todo el personal", "cat-jefaturas": "Jefaturas", "cat-clinicos": "Clínicos",
                              "cat-tecnicos": "Técnicos", "cat-administrativos": "Administrativos",
                              "role-director-cesfam": "Director/a de CESFAM", "role-oirs": "Encargado/a OIRS",
                              "sector-norte": "Sector Norte", "prog-salud-mental-adulto": "Programa Salud Mental-Adulto"
                              }.items(), groups.items())
        folders = {mount: folder["groups"] for mount, folder in self.folders().items()}
        self.assertLessEqual({
            "Transversal": {"all-staff": READ, "cat-jefaturas": MANAGE},
            "Sectores/Sector Norte": {"sector-norte": MANAGE, "cat-jefaturas": MANAGE},
            "Programas/Atención Domiciliaria": {"prog-atencion-domiciliaria": MANAGE, "cat-jefaturas": MANAGE},
            "Unidades/Farmacia": {"role-quimico-farmaceutico": MANAGE, "role-tens-farmacia": MANAGE,
                                  "cat-jefaturas": READ_SHARE},
            "Unidades/Dirección": {"cat-jefaturas": MANAGE}}.items(), folders.items())
        self.assertEqual(len([m for m in folders if m.split("/")[0] in ("Sectores", "Programas", "Unidades")]), 10)
        self.assertEqual(self.folders()["Transversal"]["files"], dict.fromkeys(
            ["Protocolos", "Flujogramas", "Documentación", "Registro de redes", "Actas de reuniones"]))

    def test_equipos_lists_the_41_cargos_instalar_makes_their_groups_and_a_person_with_a_cargo_joins_its_category(
            self):
        self.claim("999003")
        self.teams(**TEAMS)
        cargos = {role: name for roles in CARGOS.values() for role, name in roles.items()}
        listed = self.browser.get("/api/state")[1]["groups"]
        self.assertEqual([(gid, name) for gid, name in listed.items() if gid.startswith("role-")], list(cargos.items()))
        status, body = self.browser.request("POST", "/api/people", {"spreadsheet": spreadsheet(
            *(f"p{n};Persona;{n};;{role};{'sí' if n == 0 else ''}" for n, role in enumerate(cargos)))})
        self.assertEqual((status, body["errors"], len(body["people"])), (200, [], 41))
        self.install()
        self.until("done")
        self.assertEqual({gid: group["name"] for gid, group in self.suite.groups.items() if gid.startswith("role-")},
                         cargos)
        for n, (role, category) in enumerate((role, category) for category, roles in CARGOS.items() for role in roles):
            self.assertEqual(self.member_of(f"p{n}") - {"admin"}, {"all-staff", category, role}, role)

    def test_the_cargo_accounts_join_their_groups_and_the_passwords_file_lists_each_first_password(self):
        self.claim()
        self.teams(**TEAMS)
        self.install()
        self.until("done")
        self.assertLessEqual(CARGO, set(self.suite.users))
        members = {gid: group["members"] for gid, group in self.suite.groups.items()}
        for user, groups in (("director", ("role-director-cesfam", "cat-jefaturas", "all-staff")),
                             ("jefe.some", ("role-jefe-some", "cat-jefaturas", "all-staff")),
                             ("jefe.norte", ("role-jefe-sector-mais", "sector-norte", "cat-jefaturas", "all-staff"))):
            self.assertEqual({gid for gid, users in members.items() if user in users}, set(groups), user)
        self.assertEqual(self.suite.users["jefe.norte"]["name"], "Jefe/a de Sector Norte")
        passwords = self.browser.request("POST", "/api/close", {})[1]["passwords"]
        for user in CARGO:
            self.assertIn(f"  Usuario: {user}\n  Contraseña: {self.suite.users[user]['password']}\n", passwords)
        self.assertEqual(len({self.suite.users[user]["password"] for user in CARGO}), len(CARGO))

    def test_instalar_makes_exactly_the_cargo_accounts_group_admins_of_all_staff_a_sector_added_on_equipos_too(self):
        self.claim()
        self.teams(**TEAMS)
        self.teams(sectors=[*TEAMS["sectors"], "Oriente"], programs=TEAMS["programs"])
        self.install()
        self.until("done")
        self.assertEqual(self.suite.sub_admins, dict.fromkeys(CARGO | {"jefe.oriente"}, {"all-staff"}))

    def test_the_end_of_install_check_finds_a_missing_and_an_extra_group_admin_and_reintentar_restores_the_set(self):
        self.claim()
        self.teams(**TEAMS)
        self.suite.answering = False
        self.install()
        self.until_checked("Dirección de la suite")
        del self.suite.sub_admins["jefe.norte"]
        self.suite.sub_admins["director"].add("cat-clinicos")
        self.suite.sub_admins["ana"] = {"all-staff"}
        self.suite.answering = True
        status, reason = self.steps(self.until("failed"))["Cuentas de cargo"]
        self.assertEqual(status, "failed")
        self.assertIn("ana, director, jefe.norte", reason)
        self.install()
        self.until("done")
        self.assertEqual(self.suite.sub_admins, dict.fromkeys(CARGO, {"all-staff"}))

    def test_every_staff_member_lands_on_the_welcome_page_which_names_the_establishment(self):
        self.claim()
        self.install()
        self.until("done")
        self.assertEqual(self.suite.system["defaultapp"], "intravox")
        intranet = self.folders()["Intranet"]
        self.assertLessEqual({"all-staff": READ, "cat-jefaturas": EDIT, "role-oirs": EDIT}.items(),
                             intranet["groups"].items())
        home = json.loads(intranet["files"]["es/home.json"])
        self.assertEqual(home["title"], "Inicio")
        self.assertIn("Centro de Salud Familiar Los Castaños", json.dumps(home, ensure_ascii=False))
        self.assertEqual(json.loads(intranet["files"]["es/navigation.json"])["items"][0]["uniqueId"], home["uniqueId"])

    def test_the_end_of_install_check_finds_an_undeclared_grant_and_reintentar_revokes_it_keeping_every_password(self):
        self.claim()
        self.teams(**TEAMS)
        self.suite.answering = False
        self.install()
        self.until_checked("Dirección de la suite")
        north = self.folders()["Sectores/Sector Norte"]
        north["groups"]["all-staff"] = MANAGE
        first = {user: self.suite.users[user]["password"] for user in CARGO}
        self.suite.answering = True
        status, reason = self.steps(self.until("failed"))[FOLDERS_STEP]
        self.assertEqual(status, "failed")
        self.assertIn("Sectores/Sector Norte", reason)
        self.install()
        self.until("done")
        self.assertEqual(north["groups"], {"sector-norte": MANAGE, "cat-jefaturas": MANAGE})
        self.assertEqual({user: self.suite.users[user]["password"] for user in CARGO}, first)

    def test_a_failed_optional_app_leaves_the_folders_and_the_welcome_page_green(self):
        optional = next(app for app in RELEASED if app.get("optional"))
        self.suite.app_releases[optional["repo"]] = {}
        self.claim()
        self.install(optional=[optional["id"]])
        steps = self.steps(self.until("failed"))
        self.assertEqual(steps[APPS_STEP][0], "failed")
        self.assertIn(f"«{optional['name']}»", steps[APPS_STEP][1])
        for name in (FOLDERS_STEP, WELCOME_STEP, "Grupos y equipos", "Cuentas de cargo"):
            self.assertEqual(steps[name], ("green", None), name)
        self.assertIn("Intranet", self.folders())

    def test_without_groupfolders_the_folders_and_the_welcome_page_fail_naming_it(self):
        self.suite.store.remove("groupfolders")
        self.claim()
        self.install()
        steps = self.steps(self.until("failed"))
        for name in (FOLDERS_STEP, WELCOME_STEP):
            self.assertEqual(steps[name][0], "failed", name)
            self.assertIn("«Carpetas de equipo»", steps[name][1], name)
        self.assertEqual((steps["Grupos y equipos"][0], steps["Cuentas de cargo"][0]), ("green", "green"))
        self.assertEqual(self.suite.group_folders, {})

    def test_a_sector_named_as_a_position_gets_its_own_jefatura(self):
        self.claim()
        self.teams(sectors=["Farmacia", "SOME"])
        accounts = {a["user"]: a["name"] for a in self.browser.get("/api/state")[1]["cargo_accounts"]}
        self.assertLessEqual({"jefe.farmacia": "Jefe/a de Farmacia (Químico/a Farmacéutico/a)",
                              "jefe.sector-farmacia": "Jefe/a de Sector Farmacia", "jefe.some": "Jefe/a de SOME",
                              "jefe.sector-some": "Jefe/a de Sector SOME"}.items(), accounts.items())
        self.assertEqual(len(accounts), 6)

    def test_the_names_that_name_the_kind_of_establishment_follow_the_centres_type(self):
        self.claim("999003")
        state = self.browser.get("/api/state")[1]
        self.assertIn({"user": "director", "name": "Director/a de SAPU"}, state["cargo_accounts"])
        self.assertNotIn("CESFAM", json.dumps(state["cargo_accounts"], ensure_ascii=False))


# As Excel saves it: a byte order mark, CRLF, the empty columns and rows a sheet may carry, an email in capitals.
SPREADSHEET = ("\ufeffusuario;nombre;apellidos;correo;grupos;primer_admin;;\r\n"
               "ana.rojas;Ana María;Rojas Núñez;Ana.Rojas@Example.org;sector-norte role-enfermeria;sí;;\r\n"
               ";;;;;;;\r\n"
               "pedro.munoz;Pedro;Muñoz  Tapia;pedro.munoz@example.org;sector-sur, prog-atencion-domiciliaria "
               "role-medico;;;\r\n")
PEOPLE = [{"user": "ana.rojas", "name": "Ana María Rojas Núñez", "email": "ana.rojas@example.org",
           "groups": ["sector-norte", "role-enfermeria"], "admin": True},
          {"user": "pedro.munoz", "name": "Pedro Muñoz Tapia", "email": "pedro.munoz@example.org",
           "groups": ["sector-sur", "prog-atencion-domiciliaria", "role-medico"], "admin": False}]
PEOPLE_STEP = "Cuentas del personal"
CHANGED = people.CHANGED


def spreadsheet(*lines):
    return "usuario;nombre;apellidos;correo;grupos;primer_admin\n" + "".join(f"{line}\n" for line in lines)


# Ana's surname and email corrected; Pedro moved from Sector Sur and Atención Domiciliaria to Sector Norte.
CORRECTED = spreadsheet("ana.rojas;Ana María;Rojas Muñoz;ana.rojas@salud.example.org;sector-norte role-enfermeria;sí",
                        "pedro.munoz;Pedro;Muñoz Tapia;pedro.munoz@example.org;sector-norte role-medico;")


class PeopleTest(InstallTest):
    """A claimed centre with its teams, where the operator uploads the people spreadsheet."""

    def setUp(self):
        super().setUp()
        self.claim()
        self.browser.request("POST", "/api/teams", TEAMS)

    def upload(self, text):
        return self.browser.request("POST", "/api/people", {"spreadsheet": text})

    def errors(self, text):
        """The errors an upload of `text` shows, once it loads nobody."""
        status, body = self.upload(text)
        self.assertEqual((status, body["people"]), (200, []))
        return body["errors"]


class People(PeopleTest):
    """The people spreadsheet on «Equipos»: the operator downloads it blank, fills it in and uploads it; every error is
    named by its line, and the people list shows everyone the install will create. The install creates each account
    with its display name, email and groups, the first administrator's admin rights among them, and a first password
    the passwords file lists, made once."""

    def test_the_blank_spreadsheet_has_the_columns_and_loads_nobody(self):
        status, body = self.browser.get("/api/spreadsheet")
        self.assertEqual(status, 200)
        self.assertTrue(body["file"].endswith(".csv"))
        self.assertEqual(body["text"].lstrip("\ufeff").splitlines(),
                         ["usuario;nombre;apellidos;correo;grupos;primer_admin"])
        self.assertEqual(len(self.errors(body["text"])), 1)

    def test_the_people_list_shows_everyone_the_install_will_create_with_the_groups_the_spreadsheet_may_name(self):
        status, body = self.upload(SPREADSHEET)
        self.assertEqual((status, body["people"], body["errors"]), (200, PEOPLE, []))
        state = self.browser.get("/api/state")[1]
        self.assertEqual((state["people"], state["errors"]), (PEOPLE, []))
        self.assertLessEqual({"all-staff": "Todo el personal", "role-medico": "Médico General / de Familia",
                              "sector-norte": "Sector Norte"}.items(), state["groups"].items())
        self.assertNotIn("admin", state["groups"])

    def test_every_error_is_named_by_its_line_in_spanish_and_the_spreadsheet_loads_nobody(self):
        errors = self.errors(spreadsheet(
            "Ana Rojas;Ana;Rojas;;;sí",  # 2
            "luis;Luis;;;;",  # 3
            "marta;Marta;Díaz;marta@ejemplo;;",  # 4
            "marta;Marta;Díaz;;;",  # 5
            "jose;José;Pérez;;sector-oriente admin;",  # 6
            "rosa;Rosa;Soto;;;quizás",  # 7
            "carla;Carla;Vera;;;;extra",  # 8
            "juan;Juan Pablo de la Santísima Trinidad;González Hernández de los Ríos y Valdivieso;;;",  # 9
            '"ema";"Ema\nLuz";Mora;;;',  # 10 and 11
            "tomas;Tomás;Ruiz;;;si"))  # 12
        self.assertEqual([error.split(":")[0] for error in errors], [
            "Línea 2", "Línea 3", "Línea 4", "Línea 5", "Línea 6", "Línea 6", "Línea 7", "Línea 8", "Línea 9",
            "Línea 10", "Línea 12"])
        for line, value in ((2, "«ana rojas»"), (4, "«marta@ejemplo»"), (5, "línea 4"), (6, "«sector-oriente»"),
                            (7, "«quizás»"), (12, "línea 2")):
            self.assertIn(value, next(e for e in errors if e.startswith(f"Línea {line}:")), line)
        self.assertIn("«admin»", errors[5])
        self.assertEqual(self.browser.get("/api/state")[1]["people"], [])
        status, body = self.install()
        self.assertEqual(status, 409)
        self.assertIn("«Equipos»", body["error"])

    def test_the_first_line_must_name_the_columns(self):
        errors = self.errors("usuario,nombre,apellidos,correo,grupos,primer_admin\nana,Ana,Rojas,,,sí\n")
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("Línea 1:"), errors)

    def test_exactly_one_first_administrator_is_required(self):
        none = self.errors(spreadsheet("ana;Ana;Rojas;;;no", "pedro;Pedro;Muñoz;;;"))
        self.assertEqual(len(none), 1)
        self.assertIn("primer_admin", none[0])
        two = self.errors(spreadsheet("ana;Ana;Rojas;;;sí", "pedro;Pedro;Muñoz;;;Sí"))
        self.assertEqual([error.split(":")[0] for error in two], ["Línea 3"])

    def test_the_usernames_of_the_suites_administrator_and_of_every_cargo_account_are_reserved(self):
        cargo = [account["user"] for account in self.browser.get("/api/state")[1]["cargo_accounts"]]
        self.assertEqual(set(cargo), CARGO)
        reserved = ["admin", "Director", *cargo]
        errors = self.errors(spreadsheet("ana;Ana;Rojas;;;sí", *(f"{user};Ana;Rojas;;;" for user in reserved)))
        self.assertEqual([error.split(":")[0] for error in errors], [f"Línea {n}" for n in range(3, 3 + len(reserved))])
        for user, error in zip(reserved, errors):
            self.assertIn(f"«{user.lower()}» está reservado", error)
        self.assertIn("«Jefe/a de Sector Norte»", errors[reserved.index("jefe.norte")])

    def test_a_teams_change_reads_the_spreadsheet_again_and_instalar_waits_for_one_without_errors(self):
        self.upload(SPREADSHEET + "jefe.oriente;Olga;Pinto;;;\r\n")
        status, body = self.browser.request("POST", "/api/teams", {"sectors": ["Norte", "Oriente"],
                                                                  "programs": TEAMS["programs"]})
        self.assertEqual((status, body["people"]), (200, []))
        self.assertEqual([error.split(":")[0] for error in body["errors"]], ["Línea 4", "Línea 5"])
        self.assertIn("«sector-sur»", body["errors"][0])
        self.assertIn("«jefe.oriente» está reservado", body["errors"][1])
        self.assertEqual(self.install()[0], 409)
        self.assertEqual(self.browser.request("POST", "/api/teams", TEAMS)[1]["errors"], [])
        self.assertEqual(self.install()[0], 202)

    def test_there_is_no_install_without_people(self):
        self.upload("")
        status, body = self.install()
        self.assertEqual(status, 409)
        self.assertIn("planilla del personal", body["error"])
        self.assertIsNone(self.progress())

    def test_a_spreadsheet_that_is_not_text_is_refused_and_changes_nothing(self):
        self.upload(SPREADSHEET)
        status, body = self.browser.request("POST", "/api/people", {"spreadsheet": ["ana.rojas"]})
        self.assertEqual(status, 400)
        self.assertTrue(body["error"])
        self.assertEqual(self.browser.get("/api/state")[1]["people"], PEOPLE)

    def test_instalar_creates_each_account_with_its_display_name_email_and_groups_and_the_first_administrator_is_admin(
            self):
        self.upload(SPREADSHEET)
        self.install()
        self.until("done")
        self.assertEqual({user: (self.suite.users[user]["name"], self.suite.users[user]["email"])
                          for user in ("ana.rojas", "pedro.munoz")},
                         {"ana.rojas": ("Ana María Rojas Núñez", "ana.rojas@example.org"),
                          "pedro.munoz": ("Pedro Muñoz Tapia", "pedro.munoz@example.org")})
        self.assertEqual(self.member_of("ana.rojas"),
                         {"admin", "all-staff", "cat-clinicos", "role-enfermeria", "sector-norte"})
        self.assertEqual(self.member_of("pedro.munoz"), {"all-staff", "cat-clinicos", "role-medico", "sector-sur",
                                                         "prog-atencion-domiciliaria"})

    def test_the_passwords_file_lists_each_persons_first_password(self):
        self.upload(SPREADSHEET)
        self.install()
        self.until("done")
        passwords = self.browser.request("POST", "/api/close", {})[1]["passwords"]
        for person in PEOPLE:
            password = self.suite.users[person["user"]]["password"]
            self.assertIn(f"\n{person['name']}\n  Usuario: {person['user']}\n  Contraseña: {password}\n", passwords)
        self.assertNotEqual(self.suite.users["ana.rojas"]["password"], self.suite.users["pedro.munoz"]["password"])


class PeopleChanges(PeopleTest):
    """From «Instalar» until «Listo», «Equipos» takes a corrected spreadsheet and «Retirar», and «Reintentar» applies
    them: names, emails and teams follow the spreadsheet, a moved person leaves the old team's group, and a retired
    person's account is disabled and in no team, kept with its files and shares; a dropped person listed again is
    enabled. Only the groups the install manages change, and no password does. Of what an administrator changes in
    Nextcloud, the end-of-install check flags only a listed person's disabled account the install made; at «Listo»,
    Nextcloud's Users page owns the people."""

    def setUp(self):
        super().setUp()
        self.upload(SPREADSHEET)

    def retire(self, *users):
        return self.browser.request("POST", "/api/retire", {"users": list(users)})

    def until_people(self):
        """«Instalar», held at its end-of-install check while the suite does not answer at its name: every account
        exists."""
        self.suite.answering = False
        self.install()
        self.until_checked("Dirección de la suite")

    def reintentar(self):
        """The suite answers: the end-of-install check finds the people changed, and «Reintentar» applies them."""
        self.suite.answering = True
        self.assertEqual(self.steps(self.until("failed"))[PEOPLE_STEP], ("failed", CHANGED))
        self.install()
        return self.until("done")

    def passwords(self):
        return {user: self.suite.users[user]["password"] for user in ("ana.rojas", "pedro.munoz")}

    def test_a_corrected_spreadsheet_updates_names_emails_and_teams_and_a_moved_person_leaves_the_old_team(self):
        self.until_people()
        first = self.passwords()
        self.suite.groups["comite-calidad"] = {"name": "Comité de Calidad", "members": {"pedro.munoz"}}  # an admin's
        status, body = self.upload(CORRECTED)
        self.assertEqual((status, body["errors"]), (200, []))
        self.reintentar()
        ana = self.suite.users["ana.rojas"]
        self.assertEqual((ana["name"], ana["email"]), ("Ana María Rojas Muñoz", "ana.rojas@salud.example.org"))
        self.assertEqual(self.member_of("pedro.munoz"),
                         {"all-staff", "cat-clinicos", "role-medico", "sector-norte", "comite-calidad"})
        self.assertEqual(self.passwords(), first)
        passwords = self.browser.request("POST", "/api/close", {})[1]["passwords"]
        self.assertIn(f"\nAna María Rojas Muñoz\n  Usuario: ana.rojas\n  Contraseña: {first['ana.rojas']}\n", passwords)
        self.assertNotIn("Rojas Núñez", passwords)

    def test_a_display_name_that_starts_with_a_dash_is_set_as_written(self):
        self.until_people()
        self.upload(SPREADSHEET.replace(";Ana María;", ";-Ana María;"))
        self.reintentar()
        self.assertEqual(self.suite.users["ana.rojas"]["name"], "-Ana María Rojas Núñez")

    def test_a_change_while_the_install_runs_holds_listo_until_reintentar_applies_it(self):
        """The install applies the people as they were when «Instalar» was pressed."""
        self.suite.release.clear()
        self.install()
        self.until_step("Inicio de la suite", "running")
        status, body = self.retire("pedro.munoz")
        self.assertEqual((status, body["retired"]), (200, ["pedro.munoz"]))
        self.suite.release.set()
        install = self.until("failed")
        self.assertEqual(self.steps(install)[PEOPLE_STEP], ("failed", CHANGED))
        self.assertEqual(self.named(install, "failed"), {PEOPLE_STEP})
        self.assertTrue(self.suite.users["pedro.munoz"]["enabled"])
        first = self.passwords()
        self.suite.groups["comite-calidad"] = {"name": "Comité de Calidad", "members": {"pedro.munoz"}}  # an admin's
        self.install()
        self.until("done")
        self.assertEqual((self.suite.users["pedro.munoz"]["enabled"], self.member_of("pedro.munoz")),
                         (False, {"comite-calidad"}), "disabled and in no team, the account and its files kept")
        self.assertEqual(self.passwords(), first)
        self.assertNotIn("user:delete", {command[0] for command in self.suite.commands})

    def test_a_change_while_the_people_step_runs_logs_its_failure(self):
        ran, posted = threading.Event(), threading.Event()
        real = people.run

        # No seam reaches the window inside the people step: wrap the real step.
        def run(context):
            found = real(context)
            ran.set()
            posted.wait(10)
            return found

        with mock.patch.object(people, "run", run):
            self.install()
            self.assertTrue(ran.wait(10))
            self.assertEqual(self.retire("pedro.munoz")[0], 200)
            posted.set()
            install = self.until("failed")
        self.assertEqual(self.steps(install)[PEOPLE_STEP], ("failed", CHANGED))
        texts = self.texts(install)
        self.assertEqual(texts.count(f"Paso «{PEOPLE_STEP}»: no se completó. {CHANGED}"), 1)
        self.assertNotIn(f"Paso «{PEOPLE_STEP}»: completado.", texts)

    def test_a_change_while_the_people_check_runs_holds_listo_until_reintentar_applies_it(self):
        """The change lands once the people step's check read «Equipos», before the runner records what it found."""
        self.until_people()
        read, posted = threading.Event(), threading.Event()
        real = people.check

        # No seam reaches the window inside the people check: wrap the real check.
        def check(context):
            found = real(context)
            read.set()
            posted.wait(10)
            return found

        with mock.patch.object(people, "check", check):
            self.suite.answering = True
            self.assertTrue(read.wait(10))
            self.assertEqual(self.retire("pedro.munoz")[0], 200)
            posted.set()
            self.assertEqual(self.steps(self.until("failed"))[PEOPLE_STEP], ("failed", CHANGED))
        self.install()
        self.until("done")
        self.assertEqual((self.suite.users["pedro.munoz"]["enabled"], self.member_of("pedro.munoz")), (False, set()))

    def test_a_retired_person_stays_retired_when_the_people_step_runs_again(self):
        self.retire("pedro.munoz")
        self.until_people()
        retired = (self.suite.users["pedro.munoz"]["enabled"], self.member_of("pedro.munoz"))
        self.assertEqual(retired, (False, set()), "retired before «Instalar», the account is made disabled")
        self.upload(CORRECTED)
        self.reintentar()
        self.assertEqual((self.suite.users["pedro.munoz"]["enabled"], self.member_of("pedro.munoz")), retired)
        self.assertEqual(self.suite.users["ana.rojas"]["email"], "ana.rojas@salud.example.org")
        self.assertEqual(self.browser.get("/api/state")[1]["retired"], ["pedro.munoz"])

    def test_moving_primer_admin_takes_the_old_first_administrator_out_of_admin_and_retiring_them_out_of_every_group(
            self):
        self.suite.down = {"issuer"}
        self.install()
        self.until("failed")
        self.upload(spreadsheet("ana.rojas;Ana María;Rojas Núñez;ana.rojas@example.org;sector-norte role-enfermeria;",
                                "pedro.munoz;Pedro;Muñoz Tapia;pedro.munoz@example.org;sector-sur role-medico;sí"))
        self.install()
        self.until("failed")
        self.assertNotIn("admin", self.member_of("ana.rojas"))
        self.assertIn("admin", self.member_of("pedro.munoz"))
        self.assertEqual(self.retire("ana.rojas")[0], 200)
        self.suite.down = set()
        self.install()
        self.until("done")
        self.assertEqual((self.suite.users["ana.rojas"]["enabled"], self.member_of("ana.rojas")), (False, set()))

    def test_a_corrected_spreadsheet_that_drops_a_person_whose_account_the_install_made_retires_them(self):
        self.until_people()
        self.suite.groups["comite-calidad"] = {"name": "Comité de Calidad", "members": {"pedro.munoz"}}  # an admin's
        status, body = self.upload(spreadsheet("ana.rojas;Ana;Rojas;;sector-norte;sí"))
        self.assertEqual((status, body["dropped"]), (200, [{"user": "pedro.munoz", "name": "Pedro Muñoz Tapia"}]))
        self.reintentar()
        self.assertEqual((self.suite.users["pedro.munoz"]["enabled"], self.member_of("pedro.munoz")),
                         (False, {"comite-calidad"}))

    def test_a_dropped_person_listed_again_is_enabled_in_their_teams_unless_retirar_retired_them(self):
        listed = SPREADSHEET + "luis.vera;Luis;Vera Soto;;sector-norte;\r\n"
        self.upload(listed)
        self.retire("luis.vera")
        self.until_people()
        teams = self.member_of("pedro.munoz")
        self.upload(spreadsheet("ana.rojas;Ana;Rojas;;sector-norte;sí"))
        self.suite.answering = True
        self.until("failed")
        self.suite.answering = False
        self.install()
        self.until_checked("Dirección de la suite")
        self.assertEqual([self.suite.users[user]["enabled"] for user in ("pedro.munoz", "luis.vera")], [False, False])
        self.upload(listed)
        self.reintentar()
        self.assertEqual((self.suite.users["pedro.munoz"]["enabled"], self.member_of("pedro.munoz")), (True, teams))
        self.assertEqual((self.suite.users["luis.vera"]["enabled"], self.member_of("luis.vera")), (False, set()))

    def test_a_dropped_person_who_never_got_an_account_is_forgotten(self):
        self.upload(SPREADSHEET + "luis.vera;Luis;Vera;;sector-norte;\r\n")
        self.upload(SPREADSHEET)
        self.install()
        self.until("done")
        self.assertEqual(self.browser.get("/api/state")[1]["dropped"], [])
        self.assertNotIn("luis.vera", self.suite.users)
        self.assertFalse([command for command in self.suite.commands if "luis.vera" in command])

    def test_an_account_made_later_in_nextcloud_and_not_in_the_spreadsheet_is_untouched(self):
        self.until_people()
        self.suite.add_user("rosa.soto", "Rosa Soto", "contraseña de Rosa")
        for group in ("all-staff", "sector-norte"):
            self.suite.groups[group]["members"].add("rosa.soto")
        self.upload(CORRECTED)
        self.assertEqual(self.browser.get("/api/state")[1]["dropped"], [])
        self.reintentar()
        self.assertEqual((self.suite.users["rosa.soto"]["enabled"], self.member_of("rosa.soto")),
                         (True, {"all-staff", "sector-norte"}))
        self.assertEqual((self.suite.users["director"]["enabled"], self.member_of("director")),
                         (True, {"role-director-cesfam", "cat-jefaturas", "all-staff"}), "a cargo account stays")

    def test_retirar_takes_people_the_list_shows_and_never_the_first_administrator(self):
        for users in ("pedro.munoz", [], ["nadie"], [3]):
            status, body = self.browser.request("POST", "/api/retire", {"users": users})
            self.assertEqual(status, 400, users)
            self.assertTrue(body["error"], users)
        status, body = self.retire("pedro.munoz", "ana.rojas")
        self.assertEqual(status, 409)
        self.assertIn("Ana María Rojas Núñez", body["error"])
        self.assertEqual(self.browser.get("/api/state")[1]["retired"], [])
        self.retire("pedro.munoz")
        errors = self.errors(spreadsheet("ana.rojas;Ana;Rojas;;;", "pedro.munoz;Pedro;Muñoz;;;sí"))
        self.assertEqual([error.split(":")[0] for error in errors], ["Línea 3"])
        self.assertIn("«pedro.munoz»", errors[0])

    def test_reintentar_answers_409_while_the_spreadsheet_has_errors(self):
        self.suite.down = {"issuer"}
        self.install()
        self.assertEqual(self.steps(self.until("failed"))[PEOPLE_STEP], ("green", None))
        self.upload(spreadsheet("ana.rojas;Ana;Rojas;;sector-oriente;sí"))
        self.assertEqual(self.steps(self.progress())[PEOPLE_STEP], ("failed", CHANGED))
        status, body = self.install()
        self.assertEqual(status, 409)
        self.assertIn("«Equipos»", body["error"])
        self.suite.down = set()
        self.upload(CORRECTED)
        self.assertEqual(self.install()[0], 202)
        self.until("done")
        self.assertEqual(self.suite.users["ana.rojas"]["email"], "ana.rojas@salud.example.org")

    def test_the_end_of_install_check_does_not_flag_people_added_or_changed_later_in_nextcloud(self):
        self.until_people()
        ana = self.suite.users["ana.rojas"]
        ana.update(name="Ana Rojas", email="ana@example.org")
        self.suite.groups["sector-sur"]["members"].discard("pedro.munoz")
        self.suite.groups["sector-norte"]["members"].add("pedro.munoz")
        self.suite.add_user("rosa.soto", "Rosa Soto", "contraseña de Rosa")
        self.suite.groups["all-staff"]["members"].add("rosa.soto")
        self.suite.answering = True
        self.until("done")
        self.assertEqual((ana["name"], ana["email"]), ("Ana Rojas", "ana@example.org"))
        self.assertEqual(self.member_of("pedro.munoz"), {"all-staff", "cat-clinicos", "role-medico", "sector-norte",
                                                         "prog-atencion-domiciliaria"})

    def test_the_end_of_install_check_flags_a_listed_persons_disabled_account_and_reintentar_enables_it(self):
        self.retire("pedro.munoz")
        self.upload(SPREADSHEET + "luis.vera;Luis;Vera Soto;;sector-norte;\r\n")
        self.until_people()
        self.suite.users["luis.vera"]["enabled"] = False
        self.suite.answering = True
        self.assertEqual(self.steps(self.until("failed"))[PEOPLE_STEP], ("failed", people.MISSING.format("luis.vera")))
        self.install()
        self.until("done")
        self.assertEqual([self.suite.users[user]["enabled"] for user in ("luis.vera", "pedro.munoz")], [True, False])

    def test_a_listed_persons_disabled_account_the_install_did_not_make_stays_disabled_and_listo_follows(self):
        self.suite.add_user("pedro.munoz", "Pedro Muñoz Tapia", "contraseña de Pedro")
        self.suite.users["pedro.munoz"]["enabled"] = False
        self.install()
        self.until("done")
        self.assertFalse(self.suite.users["pedro.munoz"]["enabled"])
        self.assertNotIn("pedro.munoz", self.browser.request("POST", "/api/close", {})[1]["passwords"])

    def test_at_listo_the_people_stay_as_they_are_for_nextclouds_users_page(self):
        self.install()
        self.until("done")
        for status, body in (self.upload(spreadsheet("luis;Luis;Vera;;;sí")), self.retire("pedro.munoz")):
            self.assertEqual(status, 409)
            self.assertIn("«Cuentas»", body["error"])
        state = self.browser.get("/api/state")[1]
        self.assertEqual((state["people"], state["retired"]), (PEOPLE, []))


def bootstrap_floors():
    """The cores and the memory in GiB the bootstrap requires, as aps/bootstrap/install.sh alone sets them."""
    script = (HERE.parent / "bootstrap" / "install.sh").read_text()
    return {key: int(re.search(rf"^{variable}=(\d+)$", script, re.M)[1])
            for key, variable in (("cpus", "MIN_CPUS"), ("memory_gib", "MIN_MEMORY_GIB"))}


class Recording(InstallTest):
    """Talk recording is on, with the components, on a server with the bootstrap's floors plus one recording (about 2
    cores and a 2 GiB browser), and off on any other: the operator has nothing to choose, and «Revisar» lists it when
    it is on. The fake's server is the bootstrap's minimum until a test changes it."""

    FLOORS = bootstrap_floors()
    THRESHOLD = {key: least + 2 for key, least in FLOORS.items()}

    def switched(self, cpus, memory_gib):
        """The optional containers AIO runs once «Instalar» finished on a server with these cores and memory."""
        self.suite.machine = {"cpus": cpus, "memory_gib": memory_gib}
        self.claim()
        self.install()
        self.until("done")
        return self.suite.switched

    def test_the_fake_server_is_the_bootstrap_minimum_which_does_not_record(self):
        self.assertEqual(self.suite.machine, self.FLOORS)
        self.assertEqual(self.switched(**self.FLOORS), COMPONENTS)

    def test_a_server_at_the_threshold_records_talk_calls(self):
        self.assertEqual(self.switched(**self.THRESHOLD), COMPONENTS | {"talk-recording"})

    def test_a_server_one_core_short_does_not(self):
        short = {**self.THRESHOLD, "cpus": self.THRESHOLD["cpus"] - 1}
        self.assertEqual(self.switched(**short), COMPONENTS)

    def test_a_server_one_gib_short_does_not(self):
        short = {**self.THRESHOLD, "memory_gib": self.THRESHOLD["memory_gib"] - 1}
        self.assertEqual(self.switched(**short), COMPONENTS)

    def test_revisar_lists_recording_only_on_a_server_that_records(self):
        self.claim()
        for cpus, listed in ((self.THRESHOLD["cpus"], True), (self.THRESHOLD["cpus"] - 1, False)):
            self.suite.machine = {**self.THRESHOLD, "cpus": cpus}
            self.assertEqual("Grabador de Talk" in self.browser.get("/api/state")[1]["components"], listed, cpus)


class TrustedCertificates(InstallTest):
    """Nextcloud trusts what AIO's own folder of trusted CAs holds, which AIO also gives Talk: a test build's Let's
    Encrypt staging roots, so server-side calls through the suite's name verify its staging certificate; a release
    holds none and Nextcloud imports nothing."""

    def test_in_a_test_build_nextcloud_imports_each_ca_aio_trusts(self):
        self.suite.cacerts = ["letsencrypt-staging-1.crt", "letsencrypt-staging-2.crt"]
        self.claim()
        self.install()
        self.until("done")
        self.assertEqual(self.suite.trusted, ["letsencrypt-staging-1.crt", "letsencrypt-staging-2.crt"])


class InterruptedInstall(InstallTest):
    """An install that a Provisionador restart cut short resumes by itself, from AIO's own state."""

    def restart(self):
        """The Provisionador dies while AIO starts the suite, as systemd restarts it; AIO goes on."""
        port, worker = self.server.server_address[1], self.server.app.worker
        self.stop()
        self.suite.dies = True
        self.suite.release.set()
        worker.join(5)
        self.suite.dies = False
        self.start(port)

    def interrupted_start(self, states):
        self.claim()
        self.suite.release.clear()
        self.suite.states = states
        self.install()
        self.until_step("Inicio de la suite", "running")
        deadline = time.monotonic() + 10  # the step runs a moment before it presses AIO's start
        while not self.suite.starts:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        self.assertEqual(self.suite.starts, 1)

    def test_a_restart_while_aio_starts_waits_for_it_and_never_sends_its_start_again(self):
        self.interrupted_start({"nextcloud-aio-database": ("PostgreSQL", "running"),
                                "nextcloud-aio-apache": ("Apache", "stopped")})
        self.restart()
        self.assertEqual(self.until_step("Inicio de la suite", "running")["status"], "installing")
        self.suite.states = {"nextcloud-aio-database": ("PostgreSQL", "running"),
                             "nextcloud-aio-apache": ("Apache", "running")}
        self.until("done")
        self.assertEqual(self.suite.starts, 1)

    def test_once_aio_started_the_suite_a_silent_log_names_the_containers_not_running_yet(self):
        """Apache runs as the resumed step reads the containers: AIO started them all, and the step waits for the rest."""
        clock = [0.0]
        self.interrupted_start({"nextcloud-aio-apache": ("Apache", "stopped"), "nextcloud-aio-talk": ("Talk", "stopped")})
        self.suite.states = {"nextcloud-aio-apache": ("Apache", "running"), "nextcloud-aio-talk": ("Talk", "starting")}
        # No seam reaches the heartbeat's clock: the step's readings run on the fake one.
        with mock.patch.object(start, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            self.restart()
            install = self.until_logged("Inicio de la suite: AIO inició todos los contenedores.")
            resumed = self.texts(install).index("El Provisionador se reinició y continúa la instalación.")
            self.assertIn("Inicio de la suite: AIO inició todos los contenedores.", self.texts(install)[resumed:])
            clock[0] = start.HEARTBEAT
            self.until_logged("Inicio de la suite: aún no están en ejecución: Talk.")
            self.suite.states["nextcloud-aio-talk"] = ("Talk", "running")
            self.until("done")
        self.assertEqual(self.suite.starts, 1)

    def test_a_resumed_install_says_so_in_its_log(self):
        self.interrupted_start({"nextcloud-aio-database": ("PostgreSQL", "running"),
                                "nextcloud-aio-apache": ("Apache", "stopped")})
        self.restart()
        self.suite.states = {"nextcloud-aio-database": ("PostgreSQL", "running"),
                             "nextcloud-aio-apache": ("Apache", "running")}
        texts = [line["text"] for line in self.until("done")["log"]]
        resumed = texts.index("El Provisionador se reinició y continúa la instalación.")
        self.assertEqual(texts.count(texts[resumed]), 1)
        self.assertEqual(texts[resumed + 1], "Paso «Inicio de la suite»: en curso.")

    def test_a_suite_aio_started_while_the_provisionador_was_down_counts_as_started(self):
        """Once Apache runs, AIO's login is closed: nothing is asked of AIO's page again."""
        self.interrupted_start({"nextcloud-aio-apache": ("Apache", "stopped")})
        self.suite.states = {"nextcloud-aio-apache": ("Apache", "running")}
        self.restart()
        self.assertEqual(self.steps(self.until("done"))["Inicio de la suite"], ("green", None))
        self.assertEqual(self.suite.starts, 1)


class InstallLog(InstallTest):
    """«Registro de la instalación»: a timestamped Spanish line for each «Instalar» and «Reintentar», each step that
    starts, finishes or fails with its reason, and each change in AIO's reading of a container while the suite starts;
    the latest of them, newest last, the same in the Provisionador's journal. No line carries a secret."""

    START = "Inicio de la suite"

    def test_each_step_logs_when_it_starts_and_finishes_and_the_journal_has_the_same_lines(self):
        self.claim()
        self.install()
        install = self.until("done")
        for line in install["log"]:
            self.assertEqual(set(line), {"time", "text"})
            self.assertIsNotNone(datetime.fromisoformat(line["time"]).tzinfo)
        texts = self.texts(install)
        self.assertEqual(texts[0], "«Instalar»: comienza la instalación.")
        self.assertEqual(texts[-2:], ["Comienza la comprobación final de la instalación.", "La instalación terminó."])
        for name in self.steps(install):
            started, green = f"Paso «{name}»: en curso.", f"Paso «{name}»: completado."
            self.assertEqual((texts.count(started), texts.count(green)), (1, 1), name)
            self.assertLess(texts.index(started), texts.index(green), name)
        self.assertEqual(self.journal.getvalue().splitlines(), texts)

    def test_a_failed_step_logs_its_reason_and_reintentar_adds_lines(self):
        self.claim()
        self.suite.down = {"aio"}
        self.install()
        install = self.until("failed")
        reason = self.steps(install)["Componentes"][1]
        self.assertIn(f"Paso «Componentes»: no se completó. {reason}", self.texts(install))
        self.assertIn(f"Paso «{self.START}»: aún no comienza. Espera a que se complete: «Componentes», «Copias de seguridad».",
                      self.texts(install))
        before = len(install["log"])
        self.suite.down = set()
        self.install()
        retried = self.texts(self.until("done"))[before:]
        self.assertEqual(retried[0], "«Reintentar»: se repiten los pasos que no se completaron.")
        self.assertIn("Paso «Componentes»: completado.", retried)
        self.assertNotIn("Paso «Dirección de la suite»: en curso.", retried, "a green step does not run again")

    def test_each_change_in_aios_reading_of_a_container_while_the_suite_starts_is_logged(self):
        self.claim()
        self.suite.release.clear()
        self.suite.states = {"nextcloud-aio-database": ("PostgreSQL", "running"),
                             "nextcloud-aio-nextcloud": ("Servidor de aplicaciones", "starting"),
                             "nextcloud-aio-fulltextsearch": ("Fulltextsearch", "stopped")}
        self.install()
        deadline = time.monotonic() + 10  # the step reads the containers once, then presses AIO's start
        while not self.suite.starts:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        self.suite.states["nextcloud-aio-fulltextsearch"] = ("Fulltextsearch", "starting")
        self.until_logged("Contenedor «Búsqueda de texto completo»: iniciando.")
        self.suite.release.set()
        texts = self.texts(self.until("done"))
        start, end = texts.index(f"Paso «{self.START}»: en curso."), texts.index(f"Paso «{self.START}»: completado.")
        self.assertEqual([t for t in texts[start + 1:end] if t.startswith("Contenedor")], [
            "Contenedor «PostgreSQL»: en ejecución.", "Contenedor «Servidor de aplicaciones»: iniciando.",
            "Contenedor «Búsqueda de texto completo»: en espera.",
            "Contenedor «Búsqueda de texto completo»: AIO descarga su imagen.",
            "Contenedor «Búsqueda de texto completo»: iniciando.",
            "Contenedor «Servidor de aplicaciones»: en ejecución.",
            "Contenedor «Búsqueda de texto completo»: en ejecución."])
        self.assertEqual(set(texts[start + 1:end]) - {t for t in texts if t.startswith("Contenedor")},
                         {f"{self.START}: AIO inició todos los contenedores."})
        self.assertEqual(len([t for t in texts if t.startswith("Contenedor")]), 7, "only while the suite starts")

    def test_as_aio_answers_its_start_the_log_names_each_container_whose_image_it_downloads(self):
        """In turn, by the name the progress shows, in our own words: nothing AIO wrote reaches the log."""
        self.claim()
        self.suite.states = {"nextcloud-aio-database": ("PostgreSQL", "running"),
                             "nextcloud-aio-fulltextsearch": ("Fulltextsearch", "stopped"),
                             "nextcloud-aio-apache": ("Apache & Caddy", "stopped")}
        self.install()
        texts = self.texts(self.until("done"))
        start, end = (texts.index(f"Paso «{self.START}»: en curso."),
                      texts.index(f"{self.START}: AIO inició todos los contenedores."))
        self.assertEqual([t for t in texts[start + 1:end] if "imagen" in t], [
            "Contenedor «Búsqueda de texto completo»: AIO descarga su imagen.",
            "Contenedor «Apache & Caddy»: AIO descarga su imagen."])
        self.assertEqual(len([t for t in texts if "imagen" in t]), 2, "only while AIO's start answers")
        for raw in ("Descargando", "Iniciando", "<", "&amp;"):
            self.assertNotIn(raw, "\n".join(texts))
            self.assertNotIn(raw, self.journal.getvalue())

    def test_while_aio_starts_the_suite_a_silent_log_says_how_many_containers_run(self):
        """Once the step wrote nothing for HEARTBEAT seconds while a container does not run yet."""
        clock = [0.0]
        counted = f"{self.START}: AIO descarga e inicia los contenedores; 1 de 3 en ejecución."
        self.claim()
        self.suite.release.clear()
        self.suite.states = {"nextcloud-aio-database": ("PostgreSQL", "running"),
                             "nextcloud-aio-nextcloud": ("Servidor de aplicaciones", "starting"),
                             "nextcloud-aio-fulltextsearch": ("Fulltextsearch", "stopped")}
        # No seam reaches the heartbeat's clock: the step's readings run on the fake one.
        with mock.patch.object(start, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            self.install()
            self.until_logged("Contenedor «Búsqueda de texto completo»: AIO descarga su imagen.")
            clock[0] = start.HEARTBEAT - 1
            time.sleep(start.POLL * 1.5)
            self.assertNotIn(counted, self.texts(self.progress()))
            clock[0] = start.HEARTBEAT
            self.until_logged(counted)
            self.suite.release.set()
            texts = self.texts(self.until("done"))
        self.assertEqual(texts.count(counted), 1)
        self.assertLess(texts.index(counted), texts.index(f"{self.START}: AIO inició todos los contenedores."))

    def test_the_log_keeps_its_latest_lines_newest_last(self):
        self.claim()
        self.suite.down = {"aio"}
        self.install()
        self.until("failed")
        for _ in range(runner.LINES):
            self.install()
            log = self.until("failed")["log"]
            if "«Instalar»: comienza la instalación." not in self.texts({"log": log}):
                break
        self.assertEqual(len(log), runner.LINES)
        self.assertEqual([line["time"] for line in log], sorted(line["time"] for line in log))
        self.assertIn("«Reintentar»: se repiten los pasos que no se completaron.", self.texts({"log": log}))

    def test_no_line_carries_a_password_the_passphrase_or_the_links_secret(self):
        link = self.link()
        self.browser = Browser(link.url)
        self.assertEqual(self.browser.open(link.url)[0], 200)
        self.claim()
        self.suite.down = {"nextcloud"}
        self.install()
        self.until("failed")
        self.suite.down = set()
        self.install()
        logged = "\n".join(self.texts(self.until("done")))
        passwords = [user["password"] for user in self.suite.users.values()]
        self.assertGreater(len(passwords), 1, "the install made people's accounts")
        for secret in (FakeSuite.PASSPHRASE, FakeSuite.ADMINISTRATOR, FakeSuite.BACKUP_PASSWORD,
                       urlsplit(link.url).fragment, *passwords):
            self.assertNotIn(secret, logged)
            self.assertNotIn(secret, self.journal.getvalue())


TOKENS = brand.tokens()
SUITE = "APS Conecta Gestión"
MOTTO = "La salud que compartimos es la que mejora"
HELP_PAGE = "contrasena.html"  # in the theme folder
HELP_LINK = "https://cesfam-doctor-juan-perez.gestion.apsconecta.cl/themes/apsconecta/contrasena.html"
OFF = ("nextcloud_announcements", "firstrunwizard", "survey_client", "support")


class Branding(InstallTest):
    """Every page says «APS Conecta Gestión» in the brand token file's colours and fonts: the theme folder goes into
    Nextcloud's themes folder, theming takes the name, the colours, the logo and the backdrop, and Nextcloud's own voice
    goes quiet."""

    def setUp(self):
        super().setUp()
        self.claim("999004")
        self.install()
        self.until("done")
        self.theme = self.suite.themes["apsconecta"]

    def test_the_theme_carries_the_brand_token_file_its_fonts_the_art_in_its_colours_and_the_centres_name(self):
        self.assertEqual(self.theme["brand/tokens.css"], (brand.FOLDER / "tokens.css").read_bytes())
        self.assertEqual(self.theme["brand/nunito-sans.woff2"], (brand.FOLDER / "nunito-sans.woff2").read_bytes())
        self.assertIn('@import url("../../brand/tokens.css")', self.theme["core/css/server.css"].decode())
        self.assertEqual(self.theme["core/css/site.css"].decode(),
                         ':root { --aps-establishment: "CESFAM Doctor Juan Pérez"; }\n')
        for art in ("core/img/logo.svg", "core/img/logoheader.svg", "brand/favicon.svg"):
            self.assertTrue(self.theme[art].startswith(b"<svg"), art)
            self.assertNotIn(b"var(--aps-", self.theme[art], art)
            self.assertIn(TOKENS["--aps-logo-gold"].encode(), self.theme[art], art)
        self.assertIn(TOKENS["--aps-violet"].encode(), self.theme["brand/favicon.svg"])

    def test_theming_says_aps_conecta_gestion_in_the_token_files_colours_with_the_themes_logo(self):
        theming = self.suite.apps["theming"]
        self.assertEqual(self.suite.system["theme"], "apsconecta")
        named = ("name", "productName", "primary_color", "background_color")
        self.assertEqual({key: theming.get(key) for key in named},
                         {"name": SUITE, "productName": SUITE, "primary_color": TOKENS["--aps-violet"],
                          "background_color": TOKENS["--aps-violet-dark"]})
        self.assertEqual([theming.get(f"{image}Mime") for image in ("logo", "logoheader", "favicon", "background")],
                         ["image/svg+xml"] * 3 + ["backgroundColor"])
        self.assertEqual((self.suite.system["enforce_theme"], theming["disable-user-theming"]), ("light", True))

    def test_theming_takes_the_motto_as_its_slogan(self):
        self.assertEqual(self.suite.apps["theming"]["slogan"], MOTTO)

    def test_the_theme_carries_the_lost_password_help_page_naming_the_centre(self):
        self.assertIn("CESFAM Doctor Juan Pérez", self.theme[HELP_PAGE].decode())

    def test_lost_password_opens_the_help_page_on_the_suites_direccion(self):
        self.assertEqual(self.suite.system["lost_password_link"], HELP_LINK)

    def test_nextclouds_announcements_slides_survey_support_help_signup_and_update_banners_are_off(self):
        self.assertEqual({app: self.suite.apps[app]["enabled"] for app in OFF}, dict.fromkeys(OFF, "no"))
        self.assertLessEqual({"knowledgebaseenabled": False, "simpleSignUpLink.shown": False, "updatechecker": False,
                              "skeletondirectory": "", "customclient_ios_appid": ""}.items(), self.suite.system.items())
        self.assertNotIn("nextcloud.com", self.suite.apps["theming"]["url"])

    def test_text_overrides_say_aps_conecta_gestion_where_nextclouds_spanish_names_the_suite_nextcloud(self):
        texts = {path: json.loads(data)["translations"] for path, data in self.theme.items()
                 if path.endswith("/l10n/es.json")}
        self.assertLessEqual({"core/l10n/es.json", "apps/settings/l10n/es.json", "custom_apps/spreed/l10n/es.json",
                              "custom_apps/files_antivirus/l10n/es.json", "custom_apps/fulltextsearch/l10n/es.json",
                              "custom_apps/eurooffice/l10n/es.json"}, set(texts))
        said = [value for translations in texts.values() for value in translations.values()]
        self.assertFalse([value for value in said if "Nextcloud" in str(value)])
        welcome = [v for k, v in texts["custom_apps/spreed/l10n/es.json"].items() if k.startswith("# Welcome to")]
        self.assertTrue(welcome and welcome[0].startswith("# Bienvenido/a a Talk"), welcome)

    def test_the_browser_texts_are_built_once_the_theme_is_in_place(self):
        self.assertIn("apsconecta", self.suite.texts_built[-1]["themes"])

    def test_everyones_personal_settings_link_the_source_code_and_its_licence(self):
        notice = self.theme["apps/settings/templates/settings/personal/development.notice.php"].decode()
        self.assertIn('href="https://github.com/APS-Conecta/gestion-aio"', notice)
        self.assertIn('href="https://www.gnu.org/licenses/agpl-3.0.html"', notice)
        self.assertNotIn("nextcloud.com", notice)


class CustomMenu(InstallTest):
    """The custom menu wears the brand's dark violet once its step ensured its app; without the app, its step and the
    apps step fail naming it, and nothing else waits for it."""

    def test_the_custom_menu_takes_the_brand_token_files_dark_violet(self):
        self.claim()
        self.install()
        self.until("done")
        menu = self.suite.apps["side_menu"]
        self.assertEqual((menu["background-color"], menu["background-color-to"]), (TOKENS["--aps-violet-dark"],) * 2)

    def test_without_its_app_the_menu_and_the_apps_step_fail_naming_it_and_every_other_step_goes_on(self):
        self.suite.store.remove("side_menu")
        self.claim()
        self.install()
        steps = self.steps(install := self.until("failed"))
        for step in (APPS_STEP, MENU):
            self.assertEqual(steps[step][0], "failed", step)
            self.assertIn(f"«{apps.BY_ID['side_menu']['name']}»: ", steps[step][1], step)
        self.assertEqual(self.named(install, "green"), set(steps) - {APPS_STEP, MENU})
        self.assertNotIn("side_menu", self.suite.apps, "nothing configures an app that is not there")


SEARCH, ANTIVIRUS = "Índice de búsqueda de texto completo", "Antivirus"


class SearchAndAntivirus(InstallTest):
    """Full-text search and the antivirus as AIO sets them up with their switches: the install builds the search index,
    whose files provider is the Suite manifest's fork in place of AIO's store copy, and the antivirus deletes the
    infected files it finds. When AIO turned their apps off, each step fails saying so and every other step goes on."""

    def test_instalar_builds_the_index_and_upgrades_aios_files_provider_to_the_fork(self):
        self.claim()
        self.install()
        self.until("done")
        self.assertTrue(self.suite.indexed)
        self.assertIn("files_fulltextsearch", [app for app, _ in self.suite.unpacked])
        self.assertEqual((self.suite.apps["files_fulltextsearch"]["enabled"], self.suite.outdated), ("yes", set()),
                         "Nextcloud upgraded its copy to the fork as it enabled the app again: no upgrade is pending")

    def test_the_antivirus_deletes_the_infected_files_it_finds(self):
        self.claim()
        self.install()
        self.until("done")
        self.assertEqual(self.suite.apps["files_antivirus"]["av_infected_action"], "delete")

    def test_an_app_aio_turned_off_fails_its_step_saying_so_and_every_other_step_goes_on(self):
        for app in ("files_antivirus", "fulltextsearch"):
            self.suite.apps[app]["enabled"] = "no"
        self.claim()
        self.install()
        steps = self.steps(install := self.until("failed"))
        self.assertIn("ClamAV no respondió a tiempo", steps[ANTIVIRUS][1])
        self.assertIn("búsqueda de texto completo porque su contenedor no respondió a tiempo", steps[SEARCH][1])
        self.assertEqual(self.named(install, "failed"), {SEARCH, ANTIVIRUS})
        self.assertFalse(self.suite.indexed)

    def test_a_corrupted_fork_release_fails_the_search_step_naming_the_fork_and_why(self):
        fork, package = apps.BY_ID["files_fulltextsearch"], "files_fulltextsearch.tar.gz"
        tag, assets = next(iter(self.suite.app_releases[fork["repo"]].items()))
        self.suite.app_releases[fork["repo"]] = {tag: {**assets, package: assets[package] + b"\0"}}
        self.claim()
        self.install()
        status, reason = self.steps(self.until("failed"))[SEARCH]
        self.assertEqual(status, "failed")
        self.assertRegex(reason, f"^«{fork['name']}»: [^«]*suma de verificación")
        self.assertFalse(self.suite.indexed)


OFFICE = "Office con Euro-Office"


class Office(InstallTest):
    """Nextcloud's own Office entry, on for every staff member, and Euro-Office, which AIO's Nextcloud container
    installs, opening its documents by default; Nextcloud and Euro-Office reach each other inside the suite."""

    def test_instalar_opens_the_office_entry_to_everyone_with_euro_office_as_its_editor(self):
        self.suite.apps["office"]["enabled"] = '["admin"]'  # only an admin group would see the entry
        self.claim()
        self.install()
        self.assertEqual(self.steps(self.until("done"))[OFFICE], ("green", None))
        eurooffice = self.suite.apps["eurooffice"]
        self.assertEqual((self.suite.apps["office"]["enabled"], eurooffice["enabled"]), ("yes", "yes"))
        odf = {"odt": True, "ods": True, "odp": True}
        self.assertEqual((json.loads(eurooffice["defFormats"]), json.loads(eurooffice["editFormats"])), (odf, odf))
        self.assertEqual((eurooffice["DocumentServerInternalUrl"], eurooffice["StorageUrl"]),
                         ("http://nextcloud-aio-eurooffice/", "http://nextcloud-aio-apache.nextcloud-aio:23973/"))

    def test_office_waits_for_euro_office_to_answer_nextcloud(self):
        self.claim()
        self.suite.down = {"eurooffice"}
        self.install()
        self.until_step(OFFICE, "running")
        time.sleep(1.5)
        self.assertEqual(self.steps(self.progress())[OFFICE][0], "running")
        self.suite.down = set()
        self.assertEqual(self.steps(self.until("done"))[OFFICE], ("green", None))


class Listo(InstallTest):
    """«Listo» hands over the passwords file, kept root-only on the server, and the wizard closes: the link and every
    session stop working and the Provisionador stops."""

    def command(self, *args):
        r = subprocess.run([sys.executable, "-B", str(HERE / "provisionador.py"), *args, "--state", str(self.state)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_the_passwords_file_is_handed_over_only_at_listo(self):
        self.claim()
        self.suite.answering = False
        self.install()
        self.until_checked("Dirección de la suite")
        status, body = self.browser.request("POST", "/api/close", {})
        self.assertEqual(status, 409)
        self.assertIn("«Listo»", body["error"])
        self.assertEqual(self.browser.get("/api/state")[0], 200)

    def test_listo_hands_over_the_passwords_file_and_the_wizard_closes(self):
        self.claim()
        self.install()
        self.until("done")
        status, body = self.browser.request("POST", "/api/close", {})
        self.assertEqual(status, 200)
        passwords = body["passwords"]
        for text in (f"https://{NAME}", "admin", FakeSuite.ADMINISTRATOR, FakeSuite.PASSPHRASE):
            self.assertIn(text, passwords)
        kept = self.state / "contrasenas.txt"
        self.assertEqual((kept.read_text(encoding="utf-8"), kept.stat().st_mode & 0o777), (passwords, 0o600))
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive(), "the Provisionador stops")

        port = self.server.server_address[1]
        self.stop()
        self.start(port)
        self.assertEqual(self.browser.get("/api/state")[0], 401)
        printed = self.command("link")
        self.assertIn(f"https://{NAME}", printed)
        self.assertIn(str(kept), printed)
        self.assertNotIn("#", printed)
        self.assertIn("se cerró", self.command("serve"))

    def test_the_passwords_file_lists_the_backup_encryption_password_aio_holds_to_restore_a_backup(self):
        self.suite.backup_password = "cifrado-que-aio-genero"
        self.claim()
        self.install()
        self.until("done")
        passwords = self.browser.request("POST", "/api/close", {})[1]["passwords"]
        self.assertIn("\nCopias de seguridad: contraseña necesaria para restaurar una copia en este u otro servidor\n"
                      "  Contraseña de cifrado: cifrado-que-aio-genero\n", passwords)

    def test_without_the_backup_encryption_password_the_passwords_file_says_so_and_is_not_written(self):
        self.suite.backup_password = None
        self.claim()
        self.install()
        install = self.until("failed")
        failed = {"Archivo de contraseñas", "Cuentas de cargo", "Cuentas del personal"}
        self.assertEqual(self.named(install, "failed"), failed)
        reasons = {self.steps(install)[name][1] for name in failed}
        self.assertEqual(len(reasons), 1, reasons)
        reason = reasons.pop()
        self.assertIn("contraseña de cifrado de las copias de seguridad", reason)
        self.assertIn("Reintentar", reason)
        self.assertFalse((self.state / "contrasenas.txt").exists())
        self.suite.backup_password = FakeSuite.BACKUP_PASSWORD
        self.install()
        self.until("done")
        self.assertIn(f"  Contraseña de cifrado: {FakeSuite.BACKUP_PASSWORD}\n",
                      self.browser.request("POST", "/api/close", {})[1]["passwords"])


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
