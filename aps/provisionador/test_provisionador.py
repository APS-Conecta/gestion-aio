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
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit

import brand
import provisionador
from steps import apps
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
                             ("POST", "/api/address"), ("POST", "/api/teams"), ("POST", "/api/install")):
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


COMPONENTS = {"talk", "eurooffice", "imaginary", "clamav", "fulltextsearch", "harp"}
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
        self.browser.request("POST", "/api/centre", {"code": code})
        self.assertEqual(self.browser.request("POST", "/api/address", {})[0], 200)

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
            "Talk", "Euro-Office", "Imaginary", "ClamAV (antivirus)", "Búsqueda de texto completo",
            "HaRP (servidor de aplicaciones externas)"])
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
        self.assertEqual(self.suite.starts, 1)
        self.assertLessEqual({
            "default_language": "es", "force_language": "es", "default_locale": "es_CL", "default_phone_region": "CL",
            "default_timezone": "America/Punta_Arenas", "logtimezone": "America/Punta_Arenas",
            "remember_login_cookie_lifetime": 0, "session_lifetime": 900, "session_keepalive": False,
            "maintenance_window_start": 5}.items(), self.suite.system.items())
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
        self.assertFalse(OPTIONAL_APPS & set(self.suite.apps), "no optional app is installed unless ticked")
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
        self.assertFalse({"estadistica", "totem"} & set(self.suite.apps))
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
        self.assertEqual(len([gid for gid in groups if gid.startswith("role-")]), 23)
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

    def test_a_suite_aio_started_while_the_provisionador_was_down_counts_as_started(self):
        """Once Apache runs, AIO's login is closed: nothing is asked of AIO's page again."""
        self.interrupted_start({"nextcloud-aio-apache": ("Apache", "stopped")})
        self.suite.states = {"nextcloud-aio-apache": ("Apache", "running")}
        self.restart()
        self.assertEqual(self.steps(self.until("done"))["Inicio de la suite"], ("green", None))
        self.assertEqual(self.suite.starts, 1)


TOKENS = brand.tokens()
SUITE = "APS Conecta Gestión"
OFF = ("nextcloud_announcements", "firstrunwizard", "survey_client", "support")
TEXTS = json.loads((HERE.parent / "theme" / "texts.json").read_text(encoding="utf-8"))
PLURAL = "nplurals=2; plural=(n != 1);"
# Each folder's own Spanish as Nextcloud ships it, naming Nextcloud where the overrides change it; the suite does not
# hold Contacts.
SHIPPED = {folder: {"translations": {**dict.fromkeys(texts, "Nextcloud"), "Files": "Archivos"}, "pluralForm": PLURAL}
           for folder, texts in TEXTS.items() if folder != "custom_apps/contacts"}


class Branding(InstallTest):
    """Every page says «APS Conecta Gestión» in the brand token file's colours and fonts: the theme folder goes into
    Nextcloud's themes folder, theming takes the name, the colours, the logo and the backdrop, and Nextcloud's own voice
    goes quiet."""

    def setUp(self):
        super().setUp()
        self.suite.l10n = SHIPPED
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

    def test_nextclouds_announcements_slides_survey_support_help_signup_and_update_banners_are_off(self):
        self.assertEqual({app: self.suite.apps[app]["enabled"] for app in OFF}, dict.fromkeys(OFF, "no"))
        self.assertLessEqual({"knowledgebaseenabled": False, "simpleSignUpLink.shown": False, "updatechecker": False,
                              "skeletondirectory": "", "customclient_ios_appid": ""}.items(), self.suite.system.items())
        self.assertNotIn("nextcloud.com", self.suite.apps["theming"]["url"])

    def test_text_overrides_say_aps_conecta_gestion_where_nextclouds_spanish_names_the_suite_nextcloud(self):
        texts = {path: json.loads(data)["translations"] for path, data in self.theme.items()
                 if path.endswith("/l10n/es.json")}
        self.assertLessEqual({"core/l10n/es.json", "apps/settings/l10n/es.json", "custom_apps/spreed/l10n/es.json",
                              "custom_apps/files_antivirus/l10n/es.json", "custom_apps/fulltextsearch/l10n/es.json"},
                             set(texts))
        said = [value for translations in texts.values() for value in translations.values()]
        self.assertFalse([value for value in said if "Nextcloud" in str(value)])
        welcome = [v for k, v in texts["custom_apps/spreed/l10n/es.json"].items() if k.startswith("# Welcome to")]
        self.assertTrue(welcome and welcome[0].startswith("# Bienvenido/a a Talk"), welcome)

    def test_the_browser_gets_each_apps_own_spanish_with_the_overrides_on_top_in_place_of_the_apps_own(self):
        """Of an app's translations, Nextcloud's browser loads only the first file it finds, and the theme's comes
        first; an app the suite does not hold gets none, which would hide its own Spanish once it is installed."""
        for folder, shipped in SHIPPED.items():
            app = folder.rsplit("/", 1)[-1]
            script = self.theme[f"core/{app}/l10n/es.js"].decode()
            arguments = script.removeprefix("OC.L10N.register(").removesuffix(");\n")
            self.assertEqual(json.loads(f"[{arguments}]"), [app, {**shipped["translations"], **TEXTS[folder]}, PLURAL],
                             folder)
        self.assertNotIn("core/contacts/l10n/es.js", self.theme)

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
