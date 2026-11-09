# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The acceptance run's one way to open a browser: Chromium through Playwright, which trusts the suite's roots alone: in
a test build Let's Encrypt's staging roots, which its certificate comes from, added to its context; in a release the
system's real roots, as Let's Encrypt's production service issues it. The context adds trust only for those and for
the certificates a check pins after verifying them itself: the wizard's, by the fingerprint the one command printed,
and AIO's own :8080 one, read from the machine. No check ignores certificate errors. A check that calls gets fake media
devices: Chromium's fake camera and a microphone that plays a steady tone.

Checks import it after `sys.path.insert(0, <aps/acceptance>)`.

It also gives what checks read on the machine (AIO's configuration, the suite's name, the Provisionador's state and
files, and the first password the passwords file lists for an account), makes staff accounts, signs a person in at the
suite's login page and sends a staff member's own requests to Nextcloud, trusting the suite's roots alone. It hears a
page's console errors, uncaught exceptions and content-security-policy violations.
"""

import base64
import contextlib
import hashlib
import json
import math
import os
import re
import ssl
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

STAGING_ROOTS = Path(__file__).with_name("letsencrypt-staging-roots.pem")
# A test build (v0.0.0-ci.*, aps/release/package.sh) uses the name service's CI deployment and Let's Encrypt's staging
# service, whose roots alone the checks trust; a release's candidate, production's, whose roots the system holds.
TEST_BUILD = os.environ["TAG"].startswith("v0.0.0-ci.")
ROOTS = STAGING_ROOTS if TEST_BUILD else None  # None: the system's roots
AIO_DATA = "/mnt/docker-aio-config/data"  # in AIO's mastercontainer: its configuration and its other files
NEXTCLOUD = "sudo docker exec --interactive --user www-data nextcloud-aio-nextcloud"  # runs in Nextcloud's container
PROVISIONADOR_DATA = "/var/lib/aps-conecta/provisionador"  # on the machine: the Provisionador's state and its files
STORED = "Antes de retirarse.txt"  # 17-install has the retired person store it first, the username as its text
# Fake media devices, granted to every page (the headless browser refuses them otherwise), whose sound and video a
# page plays without a click.
FAKE_MEDIA = ("--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
              "--autoplay-policy=no-user-gesture-required")

# Reports each content-security-policy violation on the console, where `faults` hears it.
VIOLATIONS = """document.addEventListener('securitypolicyviolation',
  e => console.error(`securitypolicyviolation: ${e.violatedDirective} blocked ${e.blockedURI}`))"""


def machine(command, stdin=""):
    """What one command line run on the machine as the operator printed; it fails the check if it fails."""
    run = subprocess.run([os.environ["MACHINE"], command], input=stdin, capture_output=True, text=True, check=False)
    if run.returncode:
        sys.exit(f"on the machine, {command!r} failed:\n{run.stderr}")
    return run.stdout


def hardware():
    """The machine's CPU cores and memory, the memory in GiB rounded up as the bootstrap rounds it."""
    cpus, memory = machine("nproc; awk '/^MemTotal:/ {print $2}' /proc/meminfo").split()
    return {"cpus": int(cpus), "memory_gib": (int(memory) + 1048575) // 1048576}


def fingerprint(pem):
    h = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def verified(host, port, printed):
    """The certificate the server presents, if its SHA-256 fingerprint is the printed one, as the operator
    compares it before trusting the wizard."""
    pem = ssl.get_server_certificate((host, port))
    if fingerprint(pem) != printed:
        sys.exit(f"{host}:{port} presents the certificate {fingerprint(pem)}, not the printed {printed}")
    return pem


def wizard_link(output):
    """The one-time link and the fingerprint in what the one command (or the wizard's `link`) printed through
    one-command.sh, which masked the link's secret in the CI log."""
    links = re.findall(r"https://\S+#[\w-]+", output)
    printed = re.findall(r"(?:[0-9A-F]{2}:){31}[0-9A-F]{2}", output)
    if not (links and printed):
        sys.exit("no one-time link and certificate fingerprint were printed")
    return links[-1], printed[-1]


def aio_file(name):
    """One of AIO's files on the machine, as its mastercontainer holds it."""
    return machine(f"sudo docker exec nextcloud-aio-mastercontainer cat {AIO_DATA}/{name}")


def aio_config():
    """AIO's configuration on the machine, with its passphrase and secrets, which a check keeps in memory, unprinted."""
    return json.loads(aio_file("configuration.json"))


def occ(command, stdin=""):
    """What Nextcloud's command line printed for `command`, its arguments as one shell command line."""
    return machine(f"{NEXTCLOUD} php occ --no-ansi {command}", stdin)


def suite_name():
    """The suite's name, which «Dirección» claimed: AIO's domain."""
    name = aio_config().get("domain")
    if not name:
        sys.exit("the suite has no name: «Dirección» claimed none")
    return name


def provisionador_file(name):
    """One of the Provisionador's files on the machine, which root alone reads."""
    return machine(f"sudo cat {PROVISIONADOR_DATA}/{name}")


def provisionador_state():
    """The Provisionador's state on the machine: what the operator chose in the wizard."""
    return json.loads(provisionador_file("state.json"))


def first_password(passwords, user):
    """The first password the passwords file's text lists for `user`, or None; a check keeps it in memory, unprinted
    (run.sh masks it)."""
    listed = re.search(rf"\n  Usuario: {re.escape(user)}\n  Contraseña: (\S+)\n", passwords)
    return listed[1] if listed else None


def person(user, display_name):
    """A new staff account, made with occ with the run's test password (run.sh masks it), on stdin only; its password."""
    password = os.environ["TEST_PASSWORD"]
    machine(f"{NEXTCLOUD} sh -c 'read -r OC_PASS && export OC_PASS && "
            f"php occ user:add --password-from-env --display-name \"{display_name}\" {user}'", stdin=f"{password}\n")
    return password


def tls():
    """TLS as a staff member's own client verifies the suite: trusting the suite's roots alone."""
    return ssl.create_default_context(cafile=ROOTS)


def as_person(user, password, method, url, body=None, headers=None):
    """The status and body Nextcloud answers at `url` to a request the person's own client makes with their password,
    over TLS that trusts the suite's roots alone."""
    login = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(url, body, {"Authorization": f"Basic {login}", "OCS-APIRequest": "true",
                                                 **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120, context=tls()) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def spki(pem):
    """The SHA-256 of the certificate's public key, base64, as Chromium pins it."""
    key = subprocess.run(["openssl", "x509", "-pubkey", "-noout"], input=pem, capture_output=True, text=True,
                         check=True).stdout
    der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "DER"], input=key.encode(), capture_output=True,
                         check=True).stdout
    return base64.b64encode(hashlib.sha256(der).digest()).decode()


def tone(path):
    """Five seconds of a 440 Hz tone, which the fake microphone plays in a loop."""
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        out.writeframes(b"".join(struct.pack("<h", round(12000 * math.sin(2 * math.pi * 440 * n / 48000)))
                                 for n in range(5 * 48000)))
    return path


@contextlib.contextmanager
def chromium(pinned=(), args=(), media=False):
    """A Chromium browser trusting the suite's roots (in its own NSS database) and the pinned PEM certificates; with
    `media`, its camera and microphone are the fake ones."""
    with tempfile.TemporaryDirectory() as home, sync_playwright() as playwright:
        nssdb = Path(home, ".pki", "nssdb")
        nssdb.mkdir(parents=True)
        subprocess.run(["certutil", "-N", "-d", f"sql:{nssdb}", "--empty-password"], check=True)
        added = ROOTS.read_text() if ROOTS else ""
        roots = re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----\n", added, re.S)
        for n, root in enumerate(roots):
            subprocess.run(["certutil", "-A", "-d", f"sql:{nssdb}", "-n", f"staging-{n}", "-t", "C,,", "-a"],
                           input=root, text=True, check=True)
        pins = [f"--ignore-certificate-errors-spki-list={','.join(spki(pem) for pem in pinned)}"] if pinned else []
        fake = [*FAKE_MEDIA, f"--use-file-for-fake-audio-capture={tone(Path(home, 'tone.wav'))}"] if media else []
        browser = playwright.chromium.launch(env={**os.environ, "HOME": home}, args=[*pins, *fake, *args])
        try:
            yield browser
        finally:
            browser.close()


def faults(page):
    """The page's console errors, uncaught exceptions and content-security-policy violations from its next load on: a
    list that fills in as they happen, which a check expects to stay empty."""
    found = []
    page.add_init_script(VIOLATIONS)
    page.on("console", lambda message: message.type == "error" and found.append(message.text))
    page.on("pageerror", lambda error: found.append(str(error)))
    return found


def log_in(page, suite, user, password):
    """Sign `user` in on the suite's login page, as staff do; it fails the check unless the page then leaves /login."""
    page.goto(f"{suite}/login")
    page.fill("#user", user)
    page.fill("#password", password)
    page.click("[data-login-form-submit]")
    page.wait_for_url(lambda url: "/login" not in url, timeout=60_000)
