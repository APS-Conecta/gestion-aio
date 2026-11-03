#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""AIO's page, opened as an administrator later opens it for backups (the suite's login link, then the
passphrase), says «APS Conecta Gestión» in Spanish: brand in the title and heading, Spanish text and
controls, the source-code link, and no «Nextcloud» in what it shows."""

import json
import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

aio = f"https://{os.environ['ADDRESS']}:8080"
evidence = Path(os.environ["EVIDENCE"])
# The token behind the suite's login link and the passphrase stay in memory: never printed.
config = json.loads(subprocess.run(
    [os.environ["MACHINE"], "sudo docker exec nextcloud-aio-mastercontainer cat /mnt/docker-aio-config/data/configuration.json"],
    check=True, capture_output=True, text=True).stdout)


def check_page(page, name, texts):
    """The page is AIO's Spanish «APS Conecta Gestión» page and shows every text (buttons included)."""
    shown = page.inner_text("body") + "\n" + "\n".join(
        page.eval_on_selector_all("input[type=submit]", "inputs => inputs.filter(i => i.offsetParent).map(i => i.value)"))
    title, lang = page.title(), page.get_attribute("html", "lang")
    missing = [text for text in ["Código fuente (AGPL-3.0)", *texts] if text not in shown]
    page.screenshot(path=evidence / f"{name}.png", mask=[page.locator("#initial-nextcloud-password, #borg-backup-password")])
    print(f"{name}: title {title!r}, lang {lang!r}, missing {missing}, shows Nextcloud: {'Nextcloud' in shown}")
    if title != "APS Conecta Gestión AIO" or lang != "es" or missing or "Nextcloud" in shown:
        sys.exit(f"AIO's {name} page is not the Spanish «APS Conecta Gestión» one")


with sync_playwright() as playwright:
    page = playwright.chromium.launch().new_page(ignore_https_errors=True, viewport={"width": 1280, "height": 2400})
    try:
        page.goto(f"{aio}/api/auth/getlogin?token={config['AIO_TOKEN']}")
    except Exception:  # its message would carry the token
        sys.exit("the suite's login link does not open AIO's login")
    check_page(page, "login", ["Inicio de sesión en APS Conecta Gestión AIO", "Inicie sesión con su frase de contraseña", "Iniciar sesión"])
    page.fill("#master-password", config["password"])
    page.click("input[type=submit]")
    page.wait_for_url("**/containers", timeout=60_000)
    page.wait_for_selector("h1")
    check_page(page, "containers", ["APS Conecta Gestión AIO v", "Cerrar sesión", "Contenedores", "Servidor de aplicaciones", "En ejecución",
                                    "Detener contenedores", "Copias de seguridad y restauración", "Enviar la ubicación de la copia de seguridad"])
