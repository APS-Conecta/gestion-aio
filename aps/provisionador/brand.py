# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The brand token file, aps/brand/tokens.css: the palette and the font pairing as CSS custom properties. A page loads
it as a stylesheet; SVG art, which a browser draws without the page's stylesheets (a favicon, Nextcloud's logo), names
a token as var(--aps-…) and is rendered with the token's value.

Run as `brand.py <file>...`, it renders each file in place, as the release does for AIO's page.
"""

import re
import sys
from pathlib import Path

FOLDER = Path(__file__).resolve().parents[1] / "brand"  # aps/brand, or brand/ in the bundle
DECLARATION = re.compile(r"(--aps-[a-z0-9-]+)\s*:\s*([^;]+?)\s*;")
TOKEN = re.compile(r"var\((--aps-[a-z0-9-]+)\)")


def tokens():
    """Each token of the brand token file, by its name, with its value."""
    return dict(DECLARATION.findall((FOLDER / "tokens.css").read_text(encoding="utf-8")))


def render(text):
    """The text with each var(--aps-…) as the token's value; a token the file does not declare is a KeyError."""
    values = tokens()
    return TOKEN.sub(lambda m: values[m.group(1)], text)


if __name__ == "__main__":
    for name in sys.argv[1:]:
        path = Path(name)
        path.write_text(render(path.read_text(encoding="utf-8")), encoding="utf-8")
