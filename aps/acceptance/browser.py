# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The acceptance run's one way to open a browser: Chromium through Playwright, whose context adds trust only for
Let's Encrypt's staging roots, which the suite's certificate comes from in CI, and for the certificates a check
pins after verifying them itself: the wizard's, by the fingerprint the one command printed, and AIO's own :8080
one, read from the machine. No check ignores certificate errors. A check that calls gets fake media devices: Chromium's
fake camera and a microphone that plays a steady tone.

Checks import it after `sys.path.insert(0, <aps/acceptance>)`.

It also gives what checks read on the machine (AIO's configuration, the suite's name), makes staff accounts and
sends a staff member's own requests to Nextcloud, trusting the staging roots alone.
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
AIO_DATA = "/mnt/docker-aio-config/data"  # in AIO's mastercontainer: its configuration and its other files
NEXTCLOUD = "sudo docker exec --interactive --user www-data nextcloud-aio-nextcloud"  # runs in Nextcloud's container
# Fake media devices, granted to every page (the headless browser refuses them otherwise), whose sound and video a
# page plays without a click.
FAKE_MEDIA = ("--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
              "--autoplay-policy=no-user-gesture-required")


def machine(command, stdin=""):
    """What one command line run on the machine as the operator printed; it fails the check if it fails."""
    run = subprocess.run([os.environ["MACHINE"], command], input=stdin, capture_output=True, text=True, check=False)
    if run.returncode:
        sys.exit(f"on the machine, {command!r} failed:\n{run.stderr}")
    return run.stdout


def hardware():
    """The machine's CPU cores and memory, the memory in GiB rounded as the bootstrap rounds it."""
    cpus, memory = machine("nproc; awk '/^MemTotal:/ {print $2}' /proc/meminfo").split()
    return {"cpus": int(cpus), "memory_gib": (int(memory) + 524288) // 1048576}


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
    """The one-time link and the fingerprint in what the one command (or the wizard's `link`) printed. The
    link's secret is masked in the CI log first."""
    links = re.findall(r"https://\S+#[\w-]+", output)
    printed = re.findall(r"(?:[0-9A-F]{2}:){31}[0-9A-F]{2}", output)
    if not (links and printed):
        sys.exit("no one-time link and certificate fingerprint were printed")
    print(f"::add-mask::{links[-1].rsplit('#', 1)[1]}", flush=True)
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


def person(user, display_name):
    """A new staff account, made with occ with the run's test password (run.sh masks it), on stdin only; its password."""
    password = os.environ["TEST_PASSWORD"]
    machine(f"{NEXTCLOUD} sh -c 'read -r OC_PASS && export OC_PASS && "
            f"php occ user:add --password-from-env --display-name \"{display_name}\" {user}'", stdin=f"{password}\n")
    return password


def as_person(user, password, method, url, body=None, headers=None):
    """The status and body Nextcloud answers at `url` to a request the person's own client makes with their password,
    over TLS that trusts the staging roots alone."""
    login = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(url, body, {"Authorization": f"Basic {login}", "OCS-APIRequest": "true",
                                                 **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120,
                                    context=ssl.create_default_context(cafile=STAGING_ROOTS)) as answer:
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
    """A Chromium browser trusting the staging roots (in its own NSS database) and the pinned PEM certificates; with
    `media`, its camera and microphone are the fake ones."""
    with tempfile.TemporaryDirectory() as home, sync_playwright() as playwright:
        nssdb = Path(home, ".pki", "nssdb")
        nssdb.mkdir(parents=True)
        subprocess.run(["certutil", "-N", "-d", f"sql:{nssdb}", "--empty-password"], check=True)
        roots = re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----\n", STAGING_ROOTS.read_text(), re.S)
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
