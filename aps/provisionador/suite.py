# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The suite port: the only code that talks to AIO, deSEC and Nextcloud's command line (occ).

The Provisionador is built with one: `Suite` in production, `FakeSuite` in the wizard-API tests. A screen
that needs the suite adds its methods to both, and the fake records what it was asked so the tests can
assert the suite state.

«Dirección» asks whether a dedyn.io name is taken, registers the free domain through upstream's deSEC flow
and asks whether the suite's reverse proxy answers at it. A registration either finishes or waits for the
operator to click deSEC's verification link; asking again resumes upstream's flow where it stopped.
"""

import json
import os
import socket
import ssl
import struct
import subprocess
from pathlib import Path

DEDYN = ".dedyn.io"
LOGIN_REFUSED = "deSEC no aceptó el correo y la contraseña de esa cuenta. Revíselos e intente nuevamente."
DOMAIN_LIMIT = ("Su cuenta de deSEC alcanzó su límite de direcciones. Elimine en desec.io una que no use, o use el "
                "correo y la contraseña de otra cuenta.")
REFUSED = "deSEC no pudo registrar la dirección. Intente nuevamente en unos minutos."
SUITE_DOWN = "El contenedor maestro de la suite no responde. Revise que Docker esté en marcha e intente nuevamente."


class Unreachable(Exception):
    """deSEC or the internet cannot be reached; the operator retries."""


class Taken(Exception):
    """Someone else holds the dedyn.io name."""


class Refused(Exception):
    """deSEC refused the request; the message says why, in Spanish."""


# Upstream's deSEC flow, run in the mastercontainer as AIO's page runs it (DesecController::Register). AIO's
# first-run setup goes first, so its passphrase exists before anything is written to its configuration. The
# operator's email and password arrive on stdin only.
REGISTER = r"""
require '/var/www/docker-aio/php/vendor/autoload.php';
$container = \AIO\DependencyInjection::GetContainer();
$container->get(\AIO\Data\Setup::class)->Setup();
$config = $container->get(\AIO\Data\ConfigurationManager::class);
$in = json_decode(stream_get_contents(STDIN), true);
$error = '';
try {
    $container->get(\AIO\Desec\DesecManager::class)->register($in['email'], $in['name'], $in['password']);
} catch (\Exception $e) {
    $error = $e->getMessage();
}
echo "\n", json_encode(['domain' => $config->domain, 'awaiting' => $config->isDesecAwaitingVerification(), 'error' => $error]);
"""


def refusal(error):
    """Upstream's English reason as the exception the wizard words in Spanish."""
    if error.startswith("Could not reach the deSEC API"):
        return Unreachable()
    if "is already taken" in error:
        return Taken()
    if "invalid email address or password" in error:
        return Refused(LOGIN_REFUSED)
    if "domain limit" in error:
        return Refused(DOMAIN_LIMIT)
    return Refused(REFUSED)


def dns_rcode(name):
    """The response code the server's resolver gives for the name's SOA: 0 when the name exists, 3 (NXDOMAIN)
    when it does not."""
    lines = (line.split() for line in Path("/etc/resolv.conf").read_text(encoding="utf-8").splitlines())
    server = next((words[1] for words in lines if len(words) > 1 and words[0] == "nameserver"), "127.0.0.1")
    query_id = os.urandom(2)
    question = b"".join(bytes([len(label)]) + label.encode() for label in name.split(".")) + struct.pack(">BHH", 0, 6, 1)
    with socket.socket(socket.AF_INET6 if ":" in server else socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(5)
        s.connect((server, 53))
        s.send(query_id + struct.pack(">5H", 0x0100, 1, 0, 0, 0) + question)
        reply = s.recv(4096)
    if reply[:2] != query_id or reply[3] & 0x0F not in (0, 3):
        raise OSError(f"the resolver gave no answer for {name}")
    return reply[3] & 0x0F


class Suite:
    """The real adapter, driving AIO, deSEC and occ on this server."""

    def name_taken(self, name):
        try:
            return dns_rcode(name + DEDYN) != 3
        except OSError as error:
            raise Unreachable() from error

    def register(self, email, name, password=""):
        try:
            run = subprocess.run(["docker", "exec", "--interactive", "--user", "www-data",
                                  "nextcloud-aio-mastercontainer", "php", "-r", REGISTER],
                                 input=json.dumps({"email": email, "name": name, "password": password}),
                                 capture_output=True, text=True, timeout=300, check=False)
            result = json.loads(run.stdout.rsplit("\n", 1)[-1])
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise Refused(SUITE_DOWN) from error
        if result["domain"] == name + DEDYN:
            return "registered"
        if result["awaiting"]:
            return "awaiting"
        raise refusal(result["error"])

    def answers(self, domain, address):
        """Whether the suite's reverse proxy presents a certificate for the domain at the server's inside
        address. The browser, not this probe, judges the certificate."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            with socket.create_connection((address, 443), timeout=3) as raw, \
                    context.wrap_socket(raw, server_hostname=domain):
                return True
        except OSError:
            return False


class FakeSuite:
    """The in-memory suite of the wizard-API tests, following upstream's deSEC flow: a new account waits for
    its email to be verified, an existing one logs in with its password, and the domain is registered once."""

    def __init__(self):
        self.reachable = True  # False: deSEC and the internet are down
        self.taken = set()  # dedyn.io names others hold
        self.accounts = {}  # verified deSEC accounts: email -> password
        self.created = []  # the accounts the suite asked deSEC to create
        self.domain = None  # the domain the suite is set to
        self.answering = False  # whether the suite's reverse proxy answers at its domain
        self.pending = None  # the account created and awaiting its verification
        self.logged_in = False

    def verify(self, email):
        """The operator clicks the link deSEC emailed."""
        self.accounts[email] = "generated"

    def online(self):
        if not self.reachable:
            raise Unreachable()

    def name_taken(self, name):
        self.online()
        return name in self.taken

    def register(self, email, name, password=""):
        if self.domain:
            return "registered"
        self.online()
        if not self.logged_in:
            if password:
                if self.accounts.get(email) != password:
                    raise Refused(LOGIN_REFUSED)
            elif self.pending is None:
                self.created.append(email)
                self.pending = email
                return "awaiting"
            elif self.pending not in self.accounts:
                return "awaiting"
            self.logged_in = True
        if name in self.taken:
            raise Taken()
        self.domain = name + DEDYN
        return "registered"

    def answers(self, domain, address):
        return self.answering and domain == self.domain
