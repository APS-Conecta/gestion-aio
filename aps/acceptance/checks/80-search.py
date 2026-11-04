#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Full-text search as staff use it: a person whose username has a dot, as cargo accounts' do, uploads a document
after the install, and Nextcloud's search finds it through full-text search by a word only its text holds. Vanilla's
index job indexes it; AIO's cron runs that job every 12 minutes, and the check runs it at once instead of waiting."""

import io
import json
import secrets
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser import as_person, occ, person, suite_name  # noqa: E402

USER, FILE = "prueba.busqueda", "acta-de-busqueda.docx"
JOB = r"OCA\FullTextSearch\Cron\Index"  # vanilla's index job
DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
PARTS = {  # a DOCX's parts but its text
    "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default '
                           'Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                           '<Default Extension="xml" ContentType="application/xml"/><Override '
                           'PartName="/word/document.xml" ContentType="application/'
                           'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
    "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship '
                   'Id="rId1" Target="word/document.xml" '
                   'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
                   '</Relationships>',
}
TEXT = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{}'
        '</w:t></w:r></w:p></w:body></w:document>')


def docx(text):
    """A DOCX of one paragraph."""
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as document:
        for part, xml in {**PARTS, "word/document.xml": TEXT.format(text)}.items():
            document.writestr(part, DECLARATION + xml)
    return packed.getvalue()


suite = f"https://{suite_name()}"
password = person(USER, "Prueba Búsqueda")
word = f"plenilunio{secrets.token_hex(3)}"  # in no other document, nor in the file's name
status, _ = as_person(USER, password, "PUT", f"{suite}/remote.php/dav/files/{USER}/{FILE}",
                      docx(f"Acta de la reunión del sector: se revisó el {word} de la semana."))
if status != 201:
    sys.exit(f"uploading {FILE} answered {status}")

jobs = json.loads(occ(f"background-job:list --class='{JOB}' --output=json"))
if not jobs:
    sys.exit(f"vanilla's index job {JOB} is not registered")
search = f"{suite}/ocs/v2.php/search/providers/fulltextsearch/search?format=json&term={word}"
deadline = time.monotonic() + 3 * 60
while True:
    occ(f"background-job:execute --force-execute {jobs[0]['id']}")
    status, body = as_person(USER, password, "GET", search)
    entries = json.loads(body)["ocs"]["data"]["entries"] if status == 200 else []
    print(f"searching {word!r} answered {status}: {[(e['title'], e['subline']) for e in entries]}")
    if any(FILE in f"{e['title']} {e['subline']}" for e in entries):
        break
    if time.monotonic() > deadline:
        sys.exit(f"full-text search did not find {FILE} by a word inside it within 3 minutes")
    time.sleep(15)
print(f"{USER} found {FILE}, uploaded after the install, by a word inside it")
