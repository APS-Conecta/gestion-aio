# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Brand gate: a brand colour is written in the brand token file only.

The brand colours are the hex colours the token file (aps/brand/tokens.css) declares. A tracked file that writes one,
in hex of any case or length, with or without alpha, or in rgb() notation, fails. Our own stylesheets, whose palette
comes from the token file only, fail on any hex or functional colour literal.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

TOKENS = "aps/brand/tokens.css"
OWN_STYLESHEETS = ("aps/provisionador/www/app.css", "aps/theme/")
HEX = re.compile(r"#([0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{4}|[0-9a-f]{3})(?![0-9a-z_-])", re.I)
RGB = re.compile(r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})", re.I)
LITERAL = re.compile(HEX.pattern + r"|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(", re.I)


def normal(hex_digits):
    """A hex colour as six lowercase digits, without its alpha."""
    digits = hex_digits.lower()[:6 if len(hex_digits) > 4 else 3]
    return "".join(c * 2 for c in digits) if len(digits) == 3 else digits


def written(line):
    """The colours a line writes, in hex or rgb() notation, as six lowercase digits."""
    return {normal(m.group(1)) for m in HEX.finditer(line)} | {
        "".join(f"{int(c):02x}" for c in m.groups()) for m in RGB.finditer(line)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    repo = Path(parser.parse_args().repo)
    try:
        brand = written((repo / TOKENS).read_text(encoding="utf-8"))
    except FileNotFoundError:
        brand = set()
    if not brand:
        print(f"brand: no brand token file with hex colours at {TOKENS}")
        return 2
    tracked = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, text=True, check=True)
    found = []
    for path in tracked.stdout.split("\0"):
        if not path or path == TOKENS:
            continue
        try:
            lines = (repo / path).read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue  # a binary file, or a submodule
        own = path.endswith(".css") and path.startswith(OWN_STYLESHEETS)
        for n, line in enumerate(lines, 1):
            if written(line) & brand:
                found.append(f"{path}:{n}: a brand colour outside {TOKENS}: {line.strip()[:120]}")
            elif own and LITERAL.search(line):
                found.append(f"{path}:{n}: a colour literal in our stylesheet, whose palette is {TOKENS}'s: "
                             f"{line.strip()[:120]}")
    if found:
        print("brand:\n" + "\n".join(found))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
