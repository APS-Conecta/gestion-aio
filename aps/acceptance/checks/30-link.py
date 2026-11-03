#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The link the one command prints points at the machine's address and opens in a browser."""

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

links = re.findall(r"https://\S+", Path(os.environ["CONSOLE"]).read_text(encoding="utf-8"))
if not links:
    sys.exit("the one command printed no link")
link = links[-1].rstrip(".,;")
if urlsplit(link).hostname != os.environ["ADDRESS"]:
    sys.exit(f"the printed link {link} does not point at the machine's address {os.environ['ADDRESS']}")
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(ignore_https_errors=True)  # the operator accepts the temporary certificate once
    response = page.goto(link)
    page.screenshot(path=Path(os.environ["EVIDENCE"]) / "link.png")
    print(f"{link} opened {page.url}: {response.status if response else 'no response'}")
    if not (response and response.ok):
        sys.exit(f"{link} does not open")
