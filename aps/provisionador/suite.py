# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The suite port: the only code that talks to AIO, the APS name service and Nextcloud's command line (occ).

The Provisionador is built with one: `Suite` in production, `FakeSuite` in the wizard-API tests. A screen
that needs the suite adds its methods to both, and the fake records what it was asked so the tests can
assert the suite state.

«Dirección» claims the centre's name from the name service, which answers a credential that changes only that
name's address records and, through acme-dns, its certificate's challenge record. The credential is stored
root-only the moment it arrives and reused, so a name is claimed once. AIO is then set to the name, with our Caddy
image as its reverse proxy, and the server's inside address is published; the host's timer publishes it again
every 10 minutes (`provisionador.py publish`).

«Instalar» drives AIO's own page as its interface does from a same-origin browser, signed in with the passphrase
AIO keeps on this server: its optional-containers form, its backup settings and its start button. The passphrase
never leaves this process. The containers' progress is AIO's own reading of their state, as its page lists them.
"""

import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CREDENTIAL = Path("/var/lib/aps-conecta/name-service.json")
UNREACHABLE = ("No se pudo conectar con el servicio de nombres de APS Conecta. Revise la conexión a internet del "
               "servidor y presione «Reintentar».")
LIMITED = ("El servicio de nombres de APS Conecta limita las direcciones que se obtienen desde una misma red cada día y "
           "cada semana. Presione «Reintentar» más tarde.")
REFUSED = "El servicio de nombres de APS Conecta rechazó la solicitud (HTTP {code}). Contacte a APS Conecta."
SUITE_DOWN = "El contenedor maestro de la suite no responde. Revise que Docker esté en marcha y presione «Reintentar»."
SUITE_REFUSED = ("El contenedor maestro de la suite rechazó la instalación (HTTP {code}). Presione «Reintentar»; si "
                 "vuelve a ocurrir, contacte a APS Conecta.")
NOT_STARTED = ("El contenedor maestro no logró iniciar todos los contenedores de la suite. Presione «Reintentar»; si "
               "vuelve a ocurrir, contacte a APS Conecta.")
MASTERCONTAINER = "nextcloud-aio-mastercontainer"
AIO_CONFIG = "/mnt/docker-aio-config/data/configuration.json"  # AIO's settings, its passphrase among them
AIO_AUTHORITY = "/mnt/docker-aio-config/caddy-internal/pki/authorities/local/root.crt"  # signs AIO's page certificate
OFFICE_SUITES = {"collabora", "onlyoffice", "eurooffice"}  # AIO's office suites, one choice among them
# How AIO's start answer ends once every container started (DockerController::getStreamingResponseHtmlEnd); when one
# fails, it stops short or Slim's error page follows.
STARTED = "</body>\n</html>"


class Taken(Exception):
    """Another installation holds the name."""


class Unreachable(Exception):
    """The name service, the internet or the mastercontainer did not do what was asked; the Spanish message says
    why."""

    def __init__(self, message=UNREACHABLE):
        super().__init__(message)


# AIO's first-run setup (so its passphrase exists), then the name as its domain (the claim vouches for it), our
# Caddy image as the reverse proxy and, among AIO's secrets, the values community-containers/caddy/caddy.json passes
# it. The values arrive on stdin only.
CONFIGURE = r"""
require '/var/www/docker-aio/php/vendor/autoload.php';
$container = \AIO\DependencyInjection::GetContainer();
$container->get(\AIO\Data\Setup::class)->Setup();
$config = $container->get(\AIO\Data\ConfigurationManager::class);
$in = json_decode(stream_get_contents(STDIN), true, 4, JSON_THROW_ON_ERROR);
$config->aioCommunityContainers = ['caddy'];
$config->setDomain($in['domain'], true);
(function (array $values): void {
    $this->set('secrets', array_merge($this->get('secrets', []), $values));
})->call($config, $in['secrets']);
"""


# The containers AIO's page lists (all but its own helpers), with AIO's reading of their state: stopped, starting
# (its port does not answer yet) or running.
CONTAINERS = r"""
ini_set('display_errors', 'stderr');
require '/var/www/docker-aio/php/vendor/autoload.php';
$definition = \AIO\DependencyInjection::GetContainer()->get(\AIO\ContainerDefinitionFetcher::class);
$listed = [];
foreach ($definition->FetchDefinition() as $c) {
    if (!$c->hideFromList) {
        $listed[] = ['name' => $c->displayName, 'state' => $c->GetStartingState()->value];
    }
}
echo json_encode($listed, JSON_THROW_ON_ERROR);
"""


class AioPage:
    """AIO's own page on this host's loopback, driven as its interface drives it from a same-origin browser: the login
    form, then the forms its page posts with the session's CSRF token. Its certificate is trusted only if AIO's internal
    authority signed it."""

    def __init__(self, authority, host):
        self.context = ssl.create_default_context(cadata=authority)
        self.context.check_hostname = False  # the loopback address: the authority alone vouches for the page
        self.host = host  # what AIO records as its own address (AIO_URL), as a browser's address bar would
        self.cookie = ""
        self.csrf = {}

    def request(self, method, path, form=None, timeout=60):
        """The status and text AIO answers."""
        headers = {"Host": self.host, "Sec-Fetch-Site": "same-origin"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        body = None
        if form is not None:
            body = urllib.parse.urlencode({**self.csrf, **form})
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection = http.client.HTTPSConnection("127.0.0.1", 8080, context=self.context, timeout=timeout)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            text = response.read().decode(errors="replace")
        except (OSError, http.client.HTTPException) as error:
            print(f"AIO {path}: {error}", file=sys.stderr)
            raise Unreachable(SUITE_DOWN) from error
        finally:
            connection.close()
        for cookie in response.headers.get_all("Set-Cookie") or ():
            self.cookie = cookie.split(";", 1)[0]  # AIO's one session cookie
        return response.status, text

    def post(self, path, form, expected=201, timeout=60):
        status, text = self.request("POST", path, form, timeout)
        if status != expected:
            print(f"AIO {path}: {status} {text[-2000:]}", file=sys.stderr)
            raise Unreachable(SUITE_REFUSED.format(code=status))
        return text

    def log_in(self, passphrase):
        """Sign in with the passphrase, then open the page after login, which joins the mastercontainer to the suite's
        network."""
        _, page = self.request("GET", "/login")
        self.csrf = dict(re.findall(r'name="(csrf_name|csrf_value)" value="([^"]*)"', page))
        self.post("/api/auth/login", {"password": passphrase})
        self.request("GET", "/containers")


class Suite:
    """The real adapter, driving AIO, the name service and occ on this server."""

    def __init__(self, services, credential=CREDENTIAL):
        """`services` is the release's services.json (aps/release/package.sh): the name service's `url`, the
        `suffix` of its names and the ACME directory, `acme_ca`, the certificates come from."""
        self.services = json.loads(Path(services).read_text(encoding="utf-8"))
        self.suffix = self.services["suffix"]
        self.credential = Path(credential)

    def call(self, path, body, headers=()):
        """The name service's JSON answer. A name in use is Taken; the claim limits, any other refusal, a server error
        or a network failure is Unreachable with its Spanish message. Refusals are logged, never a credential."""
        request = urllib.request.Request(
            self.services["url"] + path, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "aps-conecta-gestion", **dict(headers)})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            print(f"name service {path}: {error.code} {error.read(500).decode(errors='replace')}", file=sys.stderr)
            if error.code == 409:
                raise Taken() from error
            if error.code == 429 and path == "/claim":
                raise Unreachable(LIMITED) from error
            raise Unreachable(REFUSED.format(code=error.code) if error.code < 500 else UNREACHABLE) from error
        except (OSError, ValueError) as error:
            print(f"name service {path}: {error}", file=sys.stderr)
            raise Unreachable() from error

    def stored(self):
        try:
            return json.loads(self.credential.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def claimed(self):
        """The stored claim's DEIS code and name, or None before the claim."""
        credential = self.stored()
        return credential and {"code": credential["username"], "name": credential["name"]}

    def store(self, credential):
        self.credential.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.credential.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as fh:
            json.dump(credential, fh)
        os.replace(tmp, self.credential)

    def claim(self, code, name):
        """Claim the DEIS code's name unless a claim is stored, set AIO to the stored claim's name and return it. The
        name service answers the name; `name`, the slug table's, is what the fake answers."""
        credential = self.stored()
        if credential is None:
            credential = self.call("/claim", {"code": code})
            self.store(credential)
        values = {"ACMEDNS_USERNAME": credential["username"], "ACMEDNS_PASSWORD": credential["password"],
                  "ACMEDNS_SUBDOMAIN": credential["subdomain"], "ACMEDNS_SERVER_URL": credential["server_url"],
                  "ACME_CA": self.services["acme_ca"]}
        self.mastercontainer("php", "-r", CONFIGURE,
                             stdin=json.dumps({"domain": credential["name"], "secrets": values}))
        return credential["name"]

    def mastercontainer(self, *command, stdin=""):
        """What a command run in the mastercontainer as AIO's user printed. A failure is logged (what it printed is an
        error, never a secret) and is Unreachable."""
        try:
            run = subprocess.run(["docker", "exec", "--interactive", "--user", "www-data", MASTERCONTAINER, *command],
                                 input=stdin, capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"mastercontainer {command[0]}: {error}", file=sys.stderr)
            raise Unreachable(SUITE_DOWN) from error
        if run.returncode:
            print(f"mastercontainer {command[0]}: {run.stdout}{run.stderr}", file=sys.stderr)
            raise Unreachable(SUITE_DOWN)
        return run.stdout

    def aio(self):
        """AIO's page, signed in with the passphrase AIO's first-run setup (on «Dirección») stored on this server."""
        page = AioPage(self.mastercontainer("cat", AIO_AUTHORITY), f"{self.stored()['name']}:8080")
        page.log_in(json.loads(self.mastercontainer("cat", AIO_CONFIG))["password"])
        return page

    def switch(self, components):
        """Turn the given optional containers on and every other one off, with AIO's optional-containers form
        (php/templates/includes/optional-containers.twig): a switch it does not send is off, and the office suite is
        one choice."""
        form = {"options-form": "options-form", "office_suite_choice": ""}
        for component in components:
            if component in OFFICE_SUITES:
                form["office_suite_choice"] = component
            else:
                form[component] = "on"
        self.aio().post("/api/configuration", form)

    def back_up(self, folder, time):
        """AIO's own backup to the folder on this server, daily at the time (UTC), with its success notification and
        without its automatic updates: what the suite runs changes only with a suite release."""
        self.aio().post("/api/configuration", {"borg_backup_host_location": folder, "borg_remote_repo": "",
                                               "daily_backup_time": time, "success_notification": "on"})

    def start(self):
        """AIO's start button: it pulls, creates and starts every container in order, and answers once it is done."""
        text = self.aio().post("/api/docker/start", {}, expected=200, timeout=900)
        if not text.rstrip().endswith(STARTED):
            print(f"AIO did not start the suite: {text[-2000:]}", file=sys.stderr)
            raise Unreachable(NOT_STARTED)

    def containers(self):
        """Each container AIO's page lists, with its state: stopped, starting or running."""
        listed = json.loads(self.mastercontainer("php", "-r", CONTAINERS))
        return [{"name": c["name"], "state": c["state"] if c["state"] in ("starting", "running") else "stopped"}
                for c in listed]

    def publish(self, address):
        """Point the claimed name at `address`; nothing before the claim."""
        credential = self.stored()
        if credential is not None:
            self.call("/address", {"address": address},
                      {"X-Api-User": credential["username"], "X-Api-Key": credential["password"]})

    def answers(self, name, address):
        """Whether the suite's reverse proxy presents a certificate for the name at the server's inside address. The
        operator's browser, not this probe, judges the certificate."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            with socket.create_connection((address, 443), timeout=3) as raw, \
                    context.wrap_socket(raw, server_hostname=name):
                return True
        except OSError:
            return False


class FakeSuite:
    """The in-memory suite of the wizard-API tests, with the name service it claims from."""

    suffix = "gestion.apsconecta.cl"

    def __init__(self):
        self.in_use = set()  # DEIS codes whose name another installation holds
        self.down = set()  # calls in an outage: the name service's "claim" and "address", and "aio"
        self.limited = False  # the name service refuses claims over its limits
        self.claims = []  # the DEIS codes the name service gave a name
        self.credential = None  # the stored claim
        self.domain = None  # the name AIO is set to
        self.published = []  # the inside addresses published
        self.answering = False  # whether the suite's reverse proxy answers at its name
        self.switched = None  # the optional containers AIO was set to run; every other one is off
        self.backup = None  # AIO's nightly backup: (folder, time)
        self.starts = 0  # how often AIO's start button was pressed
        self.states = {}  # what AIO reports per container: stopped, starting or running
        self.release = threading.Event()  # AIO's start answers once it is set: tests clear it to watch a start
        self.release.set()

    def claim(self, code, name):
        if self.credential is None:
            if "claim" in self.down:
                raise Unreachable()
            if self.limited:
                raise Unreachable(LIMITED)
            if code in self.in_use:
                raise Taken()
            self.claims.append(code)
            self.credential = {"username": code, "name": name}
        self.domain = self.credential["name"]
        return self.domain

    def claimed(self):
        return self.credential and {"code": self.credential["username"], "name": self.credential["name"]}

    def publish(self, address):
        if self.credential is None:
            return
        if "address" in self.down:
            raise Unreachable()
        self.published.append(address)

    def answers(self, name, address):
        return self.answering and name == self.domain

    def aio(self):
        if "aio" in self.down:
            raise Unreachable(SUITE_DOWN)

    def switch(self, components):
        self.aio()
        self.switched = set(components)

    def back_up(self, folder, time):
        self.aio()
        self.backup = (folder, time)

    def start(self):
        self.aio()
        self.release.wait()
        self.starts += 1

    def containers(self):
        self.aio()
        return [{"name": name, "state": state} for name, state in self.states.items()]
