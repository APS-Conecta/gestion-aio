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
"""

import json
import os
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CREDENTIAL = Path("/var/lib/aps-conecta/name-service.json")
UNREACHABLE = ("No se pudo conectar con el servicio de nombres de APS Conecta. Revise la conexión a internet del "
               "servidor y presione «Reintentar».")
LIMITED = ("El servicio de nombres de APS Conecta limita las direcciones que se obtienen desde una misma red cada día y "
           "cada semana. Presione «Reintentar» más tarde.")
SUITE_DOWN = "El contenedor maestro de la suite no responde. Revise que Docker esté en marcha y presione «Reintentar»."


class Taken(Exception):
    """Another installation holds the name."""


class Unreachable(Exception):
    """The name service, the internet or the mastercontainer did not answer; the Spanish message says which, and the
    operator retries."""

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


class Suite:
    """The real adapter, driving AIO, the name service and occ on this server."""

    def __init__(self, services, credential=CREDENTIAL):
        """`services` is the release's services.json (aps/release/package.sh): the name service's `url`, the
        `suffix` of its names and the ACME directory, `acme_ca`, the certificates come from."""
        self.services = json.loads(Path(services).read_text(encoding="utf-8"))
        self.suffix = self.services["suffix"]
        self.credential = Path(credential)

    def call(self, path, body, headers=()):
        """The name service's JSON answer; its refusals are logged, never a credential."""
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
            raise Unreachable(LIMITED if error.code == 429 else UNREACHABLE) from error
        except (OSError, ValueError) as error:
            print(f"name service {path}: {error}", file=sys.stderr)
            raise Unreachable() from error

    def stored(self):
        try:
            return json.loads(self.credential.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def store(self, credential):
        self.credential.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.credential.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as fh:
            json.dump(credential, fh)
        os.replace(tmp, self.credential)

    def claim(self, code, name):
        """Claim the DEIS code's name, `name` (the name service's slug table is the same register's), unless a claim
        is stored, and set AIO to it."""
        credential = self.stored()
        if credential is None:
            credential = self.call("/claim", {"code": code})
            self.store(credential)
        values = {"ACMEDNS_USERNAME": credential["username"], "ACMEDNS_PASSWORD": credential["password"],
                  "ACMEDNS_SUBDOMAIN": credential["subdomain"], "ACMEDNS_SERVER_URL": credential["server_url"],
                  "ACME_CA": self.services["acme_ca"]}
        try:
            run = subprocess.run(["docker", "exec", "--interactive", "--user", "www-data",
                                  "nextcloud-aio-mastercontainer", "php", "-r", CONFIGURE],
                                 input=json.dumps({"domain": name, "secrets": values}), capture_output=True,
                                 text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Unreachable(SUITE_DOWN) from error
        if run.returncode:
            print(f"AIO did not take the name: {run.stdout}{run.stderr}", file=sys.stderr)
            raise Unreachable(SUITE_DOWN)

    def publish(self, address):
        """Point the claimed name at `address`. Returns False when no name is claimed yet."""
        credential = self.stored()
        if credential is None:
            return False
        self.call("/address", {"address": address},
                  {"X-Api-User": credential["username"], "X-Api-Key": credential["password"]})
        return True

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
        self.down = set()  # name-service calls in an outage: "claim", "address"
        self.limited = False  # the name service refuses claims over its limits
        self.claims = []  # the DEIS codes the name service gave a name
        self.credential = None  # the stored claim
        self.domain = None  # the name AIO is set to
        self.published = []  # the inside addresses published
        self.answering = False  # whether the suite's reverse proxy answers at its name

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
        self.domain = name

    def publish(self, address):
        if self.credential is None:
            return False
        if "address" in self.down:
            raise Unreachable()
        self.published.append(address)
        return True

    def answers(self, name, address):
        return self.answering and name == self.domain
