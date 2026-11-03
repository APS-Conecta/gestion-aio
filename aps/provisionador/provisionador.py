#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Provisionador: the single web wizard that runs the install, opened through the one-time link.

  provisionador.py serve   serve the wizard over HTTPS (the systemd unit the bootstrap installs)
  provisionador.py link    print a fresh one-time link to the running wizard, its QR code and the
                           certificate's fingerprint (the bootstrap's last step, on every run)
  provisionador.py publish publish the server's inside address at its claimed name (the host's timer,
                           every 10 minutes)

Its one public surface is the HTTP API under /api, which the page in www/ uses and the wizard-API tests
drive. The state lives in one JSON file on the server, so a reload, a restart or a rerun resumes it.
The link's secret and the sessions are stored only as SHA-256 hashes, and only `link` prints a secret,
to the operator's console.
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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import deis
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

    def server(self, address=("0.0.0.0", PORT)):
        """Bind the wizard, make its certificate and record where it answers, for `link`."""
        httpd = Server(address, Handler)
        httpd.app = self
        host, port = httpd.server_address[:2]
        httpd.tls, self.fingerprint = certificate(self.state.dir, host)
        with self.state.change() as s:
            s["server"] = {"host": host, "port": port, "fingerprint": self.fingerprint}
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
        """The centre's name and whether it is claimed; once claimed, whether the suite answers at it."""
        claimed, centre = state.get("address"), state.get("centre")
        if claimed:
            return {**claimed, "claimed": True, "answers": self.suite.answers(claimed["name"], lan_ip())}
        return centre and {"name": self.name(centre["code"]), "claimed": False}

    def get_state(self, query, body):
        state = self.state.read()
        return 200, {"fingerprint": self.fingerprint, "centre": state.get("centre"), "address": self.address(state)}

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
        with self.state.change() as s:
            claimed = s.get("address")
            if claimed and s["centre"]["code"] != code:
                return 409, {"error": CENTRE_KEPT.format(claimed["name"])}
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
                    self.suite.claim(centre["code"], name)
                except Taken:
                    return 409, {"error": IN_USE.format(name)}
                self.suite.publish(lan_ip())
                s["address"] = {"name": name}
        return 200, {"address": self.address(self.state.read())}

    ROUTES = {
        ("GET", "/api/state"): get_state,
        ("GET", "/api/register"): get_register,
        ("GET", "/api/establishments"): get_establishments,
        ("POST", "/api/centre"): post_centre,
        ("POST", "/api/address"): post_address,
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


def print_link(state_dir):
    state = State(state_dir)
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
    Provisionador(args.state, deis.newest(HERE.parent / "deis"), Suite(SERVICES)).server().serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
