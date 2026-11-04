#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Provisionador: the single web wizard that runs the install, opened through the one-time link.

  provisionador.py serve   serve the wizard over HTTPS (the systemd unit the bootstrap installs)
  provisionador.py link    print a fresh one-time link to the running wizard, its QR code and the
                           certificate's fingerprint (the bootstrap's last step, on every run); once the
                           wizard closed at «Listo», the suite's address instead
  provisionador.py publish publish the server's inside address at its claimed name (the host's timer,
                           every 10 minutes)

Its one public surface is the HTTP API under /api, which the page in www/ uses and the wizard-API tests
drive. The state lives in one JSON file on the server, so a reload, a restart or a rerun resumes it.
The link's secret and the sessions are stored only as SHA-256 hashes, and only `link` prints a secret,
to the operator's console. At «Listo» the wizard hands over the passwords file and closes: the link and
every session stop working, and the Provisionador stops for good.
"""

import argparse
import base64
import contextlib
import fcntl
import hashlib
import hmac
import http.cookies
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import deis
import runner
import spreadsheet
import teams
from steps import apps, backups, components, passwords
from suite import Suite, Taken, Unreachable

HERE = Path(__file__).resolve().parent
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".woff2": "font/woff2"}
# The page and what it loads, by name: no request path is ever joined to a directory.
STATIC = {f"/{p.name}": p for p in (HERE / "www").iterdir() if p.suffix in CONTENT_TYPES}
STATIC["/"] = HERE / "www" / "index.html"
# connect-src: the browser-side check that this computer opens the suite at its name.
CSP = ("default-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; "
       "connect-src 'self' https://*.gestion.apsconecta.cl; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
STATE_DIR = Path("/var/lib/aps-conecta/provisionador")
SERVICES = HERE.parent / "services.json"  # the release's name service and ACME directory
PORT = 8081  # clear of AIO's 80, 443, 3478, 8080 and 8443
COOKIE = "__Host-session"
MAX_BODY = 64 * 1024

NOT_JSON = "La solicitud no es JSON válido."
NO_SESSION = "Abra el enlace que mostró el comando de instalación."
LINK_REFUSED = ("Este enlace ya se usó o fue reemplazado. Para obtener uno nuevo, vuelva a ejecutar el "
                "comando de instalación en el servidor.")
NO_CENTRE = "Elija primero el centro: la dirección es la de su nombre."
IN_USE = ("La dirección {} ya está en uso por otra instalación. Si este establecimiento ya tenía una instalación, "
          "APS Conecta puede liberarla; después presione «Reintentar».")
CENTRE_KEPT = "El centro ya no puede cambiarse: su dirección, {}, ya se obtuvo."
NO_ADDRESS = "Obtenga primero la dirección del centro en «Dirección»."
TEAMS_KEPT = "Los equipos ya no pueden cambiarse: se presionó «Instalar»."
NOT_SPREADSHEET = "Suba la planilla del personal como texto CSV."
PEOPLE_KEPT = "La planilla del personal ya no puede cambiarse: se presionó «Instalar»."
PEOPLE_WRONG = "La planilla del personal tiene errores. Corríjalos en «Equipos» y súbala de nuevo."
NOT_OFFERED = "Elija solo entre las aplicaciones opcionales que ofrece «Revisar»."
NOT_LISTO = "El archivo de contraseñas se entrega en «Listo», cuando la instalación terminó."
CLOSED = ("El asistente de instalación se cerró en «Listo»: APS Conecta Gestión está instalada en https://{}.\n"
          "El archivo de contraseñas quedó en este servidor, legible solo por root: {}")


def plan(state):
    """The teams «Equipos» declared and, once the centre is chosen, what follows from them, named for its type: the
    cargo accounts, the groups the people spreadsheet may name, and the people the uploaded spreadsheet loads or its
    errors; as «Equipos» and «Revisar» show them."""
    declared, centre = state.get("teams", []), state.get("centre")
    if not centre:
        return {"teams": declared, "cargo_accounts": [], "groups": {}, "people": [], "errors": []}
    people, errors = spreadsheet.read(state.get("spreadsheet"), declared, centre["type"])
    return {"teams": declared, "cargo_accounts": [{"user": account["user"], "name": account["name"]}
                                                  for account in teams.cargo_accounts(declared, centre["type"])],
            "groups": teams.groups(declared, centre["type"]), "people": people, "errors": errors}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def json_object(data):
    """`data` as a JSON object, or None when it is not one."""
    try:
        body = json.loads(data)
    except (ValueError, RecursionError):
        return None
    return body if isinstance(body, dict) else None


def known(session, hashes):
    return bool(session) and any(hmac.compare_digest(digest(session), h) for h in hashes)


def random_code():
    """256 random bits, URL-safe: the link's secret and the session cookie."""
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()


def fingerprint(pem):
    """A certificate's SHA-256 fingerprint, AA:BB:…, as browsers show it."""
    h = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def lan_ip():
    """The address other machines on the network reach this server by: the default route's source."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1: a route lookup, no packet is sent
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class State:
    """The wizard's state: one JSON file, replaced whole under a lock that `serve` and `link` share."""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self.path = self.dir / "state.json"

    def read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    @contextlib.contextmanager
    def change(self):
        with open(self.dir / "state.lock", "w", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self.read()
            yield data
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)


def certificate(directory, host):
    """The wizard's temporary self-signed certificate, naming the address the link uses. It is made on
    the first start and kept, so a browser that accepted it still trusts the wizard after a restart.
    Returns the server's TLS context and the certificate's fingerprint."""
    crt, key = directory / "tls.crt", directory / "tls.key"
    if not (crt.exists() and key.exists()):
        names = dict.fromkeys(["127.0.0.1", lan_ip() if host == "0.0.0.0" else host])
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                        "-nodes", "-days", "365", "-subj", "/O=APS Conecta/CN=APS Conecta Gestion",
                        "-addext", "subjectAltName=" + ",".join(f"IP:{n}" for n in names),
                        "-keyout", str(key), "-out", str(crt)], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(crt, key)
    return context, fingerprint(crt.read_text(encoding="ascii"))


class Provisionador:
    def __init__(self, state_dir, register, suite):
        self.state = State(state_dir)
        self.register = deis.Register(register)
        self.slugs = deis.slugs(self.register.rows)
        self.suite = suite
        self.fingerprint = None
        self.install_lock = threading.Lock()
        self.worker = None  # the install running in the background, if any
        self.closed = False  # set by the answer that closes the wizard

    def server(self, address=("0.0.0.0", PORT)):
        """Bind the wizard, make its certificate and record where it answers, for `link`."""
        httpd = Server(address, Handler)
        httpd.app = self
        host, port = httpd.server_address[:2]
        httpd.tls, self.fingerprint = certificate(self.state.dir, host)
        with self.state.change() as s:
            s["server"] = {"host": host, "port": port, "fingerprint": self.fingerprint}
            install = s.get("install")
        if install and runner.unfinished(install):
            self.run_install({name for name, step in install["steps"].items() if step["status"] == "running"})
        return httpd

    def has_session(self, session):
        return known(session, self.state.read().get("sessions", []))

    def open_session(self, session, body):
        """POST /api/session {"link": <the link's secret>}: a link that matches is spent, whoever opens
        it. A browser that already has a session keeps it; otherwise the link opens a new one. Returns
        the new session, if any."""
        code = body.get("link")
        with self.state.change() as s:
            matches = isinstance(code, str) and s.get("link") and hmac.compare_digest(digest(code), s["link"])
            if matches:
                s["link"] = None
            if known(session, s.get("sessions", [])):
                return 200, {"ok": True}, None
            if not matches:
                return 401, {"error": LINK_REFUSED}, None
            new_session = random_code()
            s["sessions"] = s.get("sessions", []) + [digest(new_session)]
        return 200, {"ok": True}, new_session

    def name(self, code):
        """The establishment's name: its slug from the slug table under the name service's suffix."""
        return f"{self.slugs[code]}.{self.suite.suffix}"

    def address(self, state):
        """The centre's name and whether it is claimed."""
        claimed, centre = state.get("address"), state.get("centre")
        if claimed:
            return {**claimed, "claimed": True}
        return centre and {"name": self.name(centre["code"]), "claimed": False}

    def get_state(self, query, body):
        state = self.state.read()
        return 200, {"fingerprint": self.fingerprint, "centre": state.get("centre"), "address": self.address(state),
                     "components": list(components.COMPONENTS.values()), "backup_folder": backups.BACKUP_FOLDER,
                     "apps": [app["name"] for app in apps.MANIFEST if app["id"] not in apps.OPTIONAL],
                     "optional_apps": [{"id": app["id"], "name": app["name"], "summary": app["summary"],
                                        "chosen": app["id"] in state.get("optional", ())}
                                       for app in apps.MANIFEST if app["id"] in apps.OPTIONAL],
                     **plan(state), "install": self.progress(state)}

    def get_register(self, query, body):
        return 200, self.register.facets()

    def get_establishments(self, query, body):
        """GET /api/establishments?region=&comuna=&type=&name=: the register rows matching every filter."""
        filters = {k: v[0] for k, v in parse_qs(query).items() if k in ("region", "comuna", "type", "name")}
        return 200, self.register.search(**filters)

    def post_centre(self, query, body):
        """POST /api/centre {"code", "point"?}: the establishment, at its official point or where the
        operator moved it."""
        code = body.get("code")
        row = self.register.by_code.get(code) if isinstance(code, str) else None
        if row is None:
            return 404, {"error": "Ese establecimiento no está en el registro DEIS."}
        moved = body.get("point")
        point = None if moved is None else deis.point(moved)
        if moved is not None and point is None:
            return 400, {"error": "El punto del mapa debe tener una latitud entre -90 y 90 y una longitud "
                                  "entre -180 y 180."}
        centre = deis.centre(row, point)
        claimed = self.suite.claimed()
        if claimed and claimed["code"] != code:
            return 409, {"error": CENTRE_KEPT.format(claimed["name"])}
        with self.state.change() as s:
            s["centre"] = centre
        return 200, {"centre": centre}

    def post_address(self, query, body):
        """POST /api/address: claim the centre's name through the suite port, which reuses a claim that succeeded
        before, and publish the server's inside address. The name comes from the slug table; nothing is asked."""
        with self.state.change() as s:
            centre = s.get("centre")
            if centre is None:
                return 409, {"error": NO_CENTRE}
            if not s.get("address"):
                name = self.name(centre["code"])
                try:
                    claimed = self.suite.claim(centre["code"], name)
                except Taken:
                    return 409, {"error": IN_USE.format(name)}
                self.suite.publish(lan_ip())
                s["address"] = {"name": claimed}
        return 200, {"address": self.address(self.state.read())}

    def post_teams(self, query, body):
        """POST /api/teams {"sectors": [names], "programs": [names]}: the team each name becomes, with its group and its
        folder, and the cargo accounts they bring once the centre is chosen. «Revisar» and «Instalar» follow the last
        ones posted; once «Instalar» was pressed they stay."""
        try:
            declared = teams.declare(body.get("sectors", []), body.get("programs", []))
        except ValueError as refusal:
            return 400, {"error": str(refusal)}
        with self.state.change() as s:
            if s.get("install"):
                return 409, {"error": TEAMS_KEPT}
            s["teams"] = declared
            return 200, plan(s)

    def get_spreadsheet(self, query, body):
        """GET /api/spreadsheet: the blank people spreadsheet, for the operator to fill in."""
        return 200, {"file": spreadsheet.FILE, "text": spreadsheet.TEMPLATE}

    def post_people(self, query, body):
        """POST /api/people {"spreadsheet": its text}: the people spreadsheet, kept as uploaded. The plan then lists the
        people it loads or each of its errors, and reads it again whenever the teams change; «Instalar» waits for one
        without errors. Once «Instalar» was pressed it stays."""
        uploaded = body.get("spreadsheet")
        if not isinstance(uploaded, str):
            return 400, {"error": NOT_SPREADSHEET}
        with self.state.change() as s:
            if s.get("install"):
                return 409, {"error": PEOPLE_KEPT}
            s["spreadsheet"] = uploaded
            return 200, plan(s)

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def progress(self, state):
        """None before «Instalar»; otherwise each step, and, once AIO runs only our components, each container as AIO
        reports it, a component's under its «Revisar» name. «done» means «Listo». A reading of the containers that fails
        says why, and the next one asks again."""
        install = state.get("install")
        if not install:
            return None
        running = self.busy() or runner.unfinished(install)
        answer = {"status": "done" if install.get("listo") else "installing" if running else "failed",
                  "steps": runner.progress(install)}
        if install["steps"].get("components", {}).get("status") == "green":
            try:
                answer["containers"] = [{"name": components.named(c), "state": c["state"]}
                                        for c in self.suite.containers()]
            except Unreachable as outage:
                answer.update(containers=[], error=str(outage))
        return answer

    def run_install(self, interrupted=()):
        """The install's steps, in the background, with what they need to know."""
        state = self.state.read()
        context = SimpleNamespace(suite=self.suite, centre=state["centre"], name=state["address"]["name"],
                                  inside=lan_ip(), folder=self.state.dir, interrupted=frozenset(interrupted),
                                  optional=frozenset(state.get("optional", ())), teams=state.get("teams", []),
                                  people=plan(state)["people"])
        self.worker = threading.Thread(target=runner.run, args=(self.state, context), daemon=True)
        self.worker.start()

    def post_install(self, query, body):
        """POST /api/install {"optional": [ids]}, «Instalar» and «Reintentar»: the steps not green yet run in the
        background and GET /api/state shows their progress. The optional apps ticked on «Revisar» are those of the first
        «Instalar». While the steps run, or at «Listo», pressing it again starts nothing more."""
        optional = body.get("optional", [])
        if not (isinstance(optional, list) and all(isinstance(app, str) and app in apps.OPTIONAL for app in optional)):
            return 400, {"error": NOT_OFFERED}
        with self.install_lock:
            state = self.state.read()
            if not state.get("address"):
                return 409, {"error": NO_ADDRESS}
            if not state.get("install") and plan(state)["errors"]:
                return 409, {"error": PEOPLE_WRONG}
            if not (self.busy() or state.get("install", {}).get("listo")):
                if not state.get("install"):
                    with self.state.change() as s:
                        s["optional"] = sorted(set(optional))
                runner.begin(self.state)
                self.run_install()
        return 202, {"install": self.progress(self.state.read())}

    def post_close(self, query, body):
        """POST /api/close, at «Listo»: the passwords file, whose copy stays on the server. The wizard then closes: the
        link and every session stop working and the Provisionador stops."""
        with self.state.change() as s:
            if not s.get("install", {}).get("listo"):
                return 409, {"error": NOT_LISTO}
            kept = (self.state.dir / passwords.FILE).read_text(encoding="utf-8")
            s.update(link=None, sessions=[], closed=True)
        self.closed = True
        return 200, {"passwords": kept, "file": passwords.FILE}

    ROUTES = {
        ("GET", "/api/state"): get_state,
        ("GET", "/api/register"): get_register,
        ("GET", "/api/establishments"): get_establishments,
        ("POST", "/api/centre"): post_centre,
        ("POST", "/api/address"): post_address,
        ("POST", "/api/teams"): post_teams,
        ("GET", "/api/spreadsheet"): get_spreadsheet,
        ("POST", "/api/people"): post_people,
        ("POST", "/api/install"): post_install,
        ("POST", "/api/close"): post_close,
    }

    def call(self, method, path, query, body):
        """The route's answer; an outage of the suite's services is answered in Spanish, and the state stays as it
        was."""
        route = self.ROUTES.get((method, path))
        if route is None:
            return 404, {"error": "Ruta desconocida."}
        try:
            return route(self, query, body)
        except Unreachable as outage:
            return 503, {"error": str(outage)}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    app = None
    tls = None

    def finish_request(self, request, client_address):
        request.settimeout(30)
        try:
            with self.tls.wrap_socket(request, server_side=True) as conn:
                super().finish_request(conn, client_address)
        except OSError:
            pass  # a refused certificate or a dropped connection: nothing for the operator


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30
    server_version = "Provisionador"
    sys_version = ""

    def log_message(self, format, *args):
        pass  # no request lines: the journal carries no link and no session

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def dispatch(self, method):
        app = self.server.app
        path, _, query = self.path.partition("?")
        if method == "GET" and not path.startswith("/api/"):
            return self.send_static(path)
        body = {}
        if method == "POST":
            try:
                data = self.read_body()
            except ValueError:
                self.close_connection = True  # an unread body must not be taken for the next request
                return self.send_json(400, {"error": NOT_JSON})
            if self.headers.get_content_type() != "application/json":
                # Only JSON: another origin (AIO's pages on this host share the cookie) would need a
                # preflight, and none is ever approved.
                return self.send_json(415, {"error": "La solicitud debe enviarse en formato JSON."})
            body = json_object(data)
            if body is None:
                return self.send_json(400, {"error": NOT_JSON})
        session, new_session = self.session(), None
        if (method, path) == ("POST", "/api/session"):
            status, answer, new_session = app.open_session(session, body)
        elif not app.has_session(session):
            status, answer = 401, {"error": NO_SESSION}
        else:
            status, answer = app.call(method, path, query, body)
        cookie = f"{COOKIE}={new_session}; Path=/; Secure; HttpOnly; SameSite=Strict" if new_session else None
        self.send_json(status, answer, cookie)
        if app.closed:
            threading.Thread(target=self.server.shutdown, daemon=True).start()  # the closing answer is sent

    def session(self):
        try:
            cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return None
        return cookie[COOKIE].value if COOKIE in cookie else None

    def read_body(self):
        """The whole body, so a refused request leaves the connection in step for the next one."""
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            raise ValueError("body size")
        return self.rfile.read(length)

    def send_static(self, path):
        file = STATIC.get(path)
        if file is None:
            return self.send_json(404, {"error": "No encontrado."})
        self.send(200, CONTENT_TYPES[file.suffix], file.read_bytes(), {"Content-Security-Policy": CSP})

    def send_json(self, status, answer, cookie=None):
        data = (json.dumps(answer, ensure_ascii=False) + "\n").encode()
        headers = {"Cache-Control": "no-store"}
        if cookie:
            headers["Set-Cookie"] = cookie
        self.send(status, "application/json; charset=utf-8", data, headers)

    def send(self, status, content_type, data, headers):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)


def running_server(state, timeout=60):
    """The record of the wizard that answers now, waiting for it to start."""
    deadline = time.monotonic() + timeout
    while True:
        server = state.read().get("server")
        if server:
            host = "127.0.0.1" if server["host"] == "0.0.0.0" else server["host"]
            try:
                ssl.get_server_certificate((host, server["port"]), timeout=5)
                return state.read()["server"]  # read again: the record of the server that answered
            except OSError:
                pass
        if time.monotonic() > deadline:
            raise SystemExit("El Provisionador no responde. Revise su registro con: journalctl -u aps-provisionador")
        time.sleep(0.5)


def closed(state):
    """Once the wizard closed at «Listo», what the console says instead of a link."""
    data = state.read()
    return data.get("closed") and CLOSED.format(data["address"]["name"], state.dir / passwords.FILE)


def print_link(state_dir):
    state = State(state_dir)
    if done := closed(state):
        print(done)
        return
    server = running_server(state)
    code = random_code()
    with state.change() as s:
        s["link"] = digest(code)
    host = lan_ip() if server["host"] == "0.0.0.0" else server["host"]
    url = f"https://{host}:{server['port']}/#{code}"
    print("Abra este enlace en un computador o teléfono conectado a la red del establecimiento:\n")
    print(f"    {url}\n", flush=True)
    if shutil.which("qrencode"):
        subprocess.run(["qrencode", "-t", "ANSIUTF8", "-m", "2"], input=url.encode(), check=False)
    print("\nEl navegador advertirá que la conexión no es privada: el Provisionador usa un certificado propio")
    print("y temporal. Continúe solo si la huella SHA-256 del certificado es:\n")
    print(f"    {server['fingerprint']}\n")
    print("El enlace sirve una sola vez. Para obtener uno nuevo, vuelva a ejecutar el comando de instalación.")


def publish(suite):
    """Publish the inside address the server reports at the claimed name; nothing before the claim."""
    try:
        suite.publish(lan_ip())
    except Unreachable as outage:
        print(outage, file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("serve", "link", "publish"))
    parser.add_argument("--state", type=Path, default=STATE_DIR, help="state directory (default: %(default)s)")
    args = parser.parse_args(argv)
    if args.command == "link":
        print_link(args.state)
        return 0
    if args.command == "publish":
        return publish(Suite(SERVICES))
    if done := closed(State(args.state)):
        print(done)
        return 0
    httpd = Provisionador(args.state, deis.newest(HERE.parent / "deis"), Suite(SERVICES)).server()
    httpd.serve_forever()
    httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
