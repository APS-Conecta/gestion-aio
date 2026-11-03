# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The suite's apps, as the Suite manifest (apps.json) lists them: every default app, and the optional apps the operator
ticked on «Revisar». The apps of the components (Talk, Euro-Office, the antivirus, full-text search) are AIO's, which
installs them with each component's switch.

An own or fork app comes from its repo's newest App release that the running Nextcloud admits, proven before it is
unpacked: the app key signed its SHA256SUMS, which hold its tarball's checksum. The lock file then records its tag, and
an app the lock names is installed again at that tag, never a newer one. A store app comes from Nextcloud's open app
store, as in vanilla Nextcloud.

An app that fails is named with its reason while every other app installs; «Reintentar» installs the ones that failed.
"""

import base64
import hashlib
import json
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from suite import Unreachable, write_root_only

NAME = "Aplicaciones"
NEEDS = ("start",)
MANIFEST = json.loads((Path(__file__).resolve().parents[1] / "apps.json").read_text(encoding="utf-8"))
OPTIONAL = {app["id"] for app in MANIFEST if app.get("optional")}
LOCK = "apps.lock.json"  # in the Provisionador's folder: each installed own or fork app's tag
ED25519 = bytes.fromhex("302a300506032b6570032100")  # the DER prefix of an Ed25519 public key
FAILED = "«{}»: {}"  # an app's name and why it did not install
NO_RELEASE = "No tiene una versión publicada compatible con Nextcloud {}. Contacte a APS Conecta."
UNSIGNED = ("La versión {} no está firmada con la clave de aplicaciones de APS Conecta y no se instaló. Contacte a APS "
            "Conecta.")
ALTERED = ("El paquete de la versión {} no coincide con su suma de verificación firmada y no se instaló. Presione "
           "«Reintentar»; si vuelve a ocurrir, contacte a APS Conecta.")
DISABLED = ("Estas aplicaciones no están activadas en Nextcloud: {}. Presione «Reintentar»; si vuelve a ocurrir, "
            "contacte a APS Conecta.")


class Refused(Exception):
    """An App release that is not what the app key vouches for, or none Nextcloud admits; the message says why."""


def chosen(context):
    """Every default app and the optional ones the operator ticked, in the manifest's order."""
    return [app for app in MANIFEST if app["id"] not in OPTIONAL or app["id"] in context.optional]


def enabled(suite):
    return {app for app, values in json.loads(suite.occ("config:list"))["apps"].items()
            if values.get("enabled") == "yes"}


def read_lock(context):
    path = context.folder / LOCK
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def admits(info, major):
    """Whether an info.xml admits the Nextcloud major; one that does not read as one admits none."""
    try:
        nextcloud = ET.fromstring(info).find("dependencies/nextcloud")
        return int(nextcloud.get("min-version")) <= major <= int(nextcloud.get("max-version", major))
    except (ET.ParseError, AttributeError, TypeError, ValueError):
        return False


def newest(suite, app, major):
    """The tag of the app's newest App release whose info.xml admits the Nextcloud major. The info.xml is read before
    anything is proven: the tarball is proven before it is unpacked, and Nextcloud refuses to enable an app whose own
    info.xml does not admit it."""
    for tag in suite.releases(app["repo"]):
        if admits(suite.asset(app["repo"], tag, "info.xml"), major):
            return tag
    raise Refused(NO_RELEASE.format(major))


def signed(key, sums, signature):
    """Whether the app key signed SHA256SUMS, as openssl checks it. A signify key or signature is a comment line, then
    base64: the algorithm and the key's number (10 bytes), then the Ed25519 key or signature."""
    try:
        public = base64.b64decode(key.splitlines()[1], validate=True)
        signature = base64.b64decode(signature.decode("ascii").splitlines()[1], validate=True)
    except (IndexError, ValueError):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        files = {name: Path(tmp, name) for name in ("key", "signature", "sums")}
        files["key"].write_bytes(ED25519 + public[10:])
        files["signature"].write_bytes(signature[10:])
        files["sums"].write_bytes(sums)
        return subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-keyform", "DER", "-inkey", files["key"],
                               "-rawin", "-in", files["sums"], "-sigfile", files["signature"]],
                              capture_output=True, check=False).returncode == 0


def proven(suite, app, tag):
    """The app's tarball at the tag, once the app key vouches for it."""
    repo, package = app["repo"], f"{app['id']}.tar.gz"
    sums = suite.asset(repo, tag, "SHA256SUMS")
    if not signed(suite.app_key, sums, suite.asset(repo, tag, "SHA256SUMS.minisig")):
        raise Refused(UNSIGNED.format(tag))
    tarball = suite.asset(repo, tag, package)
    if f"{hashlib.sha256(tarball).hexdigest()}  {package}" not in sums.decode(errors="replace").splitlines():
        raise Refused(ALTERED.format(tag))
    return tarball


def install(context, app, lock, major):
    """Install one app: a store app from the store, as `occ app:enable` downloads it; an own or fork app from its
    verified App release, at the lock's tag if it names one, which the lock then records."""
    suite = context.suite
    if app["source"] == "store":
        suite.occ("app:enable", app["id"])
        return
    tag = lock.get(app["id"]) or newest(suite, app, major)
    suite.unpack(app["id"], proven(suite, app, tag))
    suite.occ("app:enable", app["id"])
    lock[app["id"]] = tag
    write_root_only(context.folder / LOCK, json.dumps(lock, indent=1))


def run(context):
    suite, lock = context.suite, read_lock(context)
    on = enabled(suite)
    major = int(json.loads(suite.occ("status", "--output=json"))["version"].split(".")[0])
    reasons = []
    for app in chosen(context):
        if app["id"] in on and (app["source"] == "store" or app["id"] in lock):
            continue
        try:
            install(context, app, lock, major)
        except (Refused, Unreachable) as failure:
            reasons.append(FAILED.format(app["name"], failure))
    return reasons


def check(context):
    on, lock = enabled(context.suite), read_lock(context)
    off = [app["name"] for app in chosen(context)
           if app["id"] not in on or (app["source"] != "store" and app["id"] not in lock)]
    return [DISABLED.format(", ".join(off))] if off else []
