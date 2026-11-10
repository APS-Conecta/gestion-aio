# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Excel workbooks (.xlsx) with the standard library alone, as a ZIP package of XML parts: written from sheets of text,
and read back sheet by sheet as Excel, LibreOffice Calc and Google Sheets save them.
"""

import io
import posixpath
import zipfile
from xml.etree import ElementTree
from xml.sax.saxutils import escape, quoteattr

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
LINKS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
PARTS = "application/vnd.openxmlformats-officedocument.spreadsheetml"
DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
LAST_ROW = 1048576  # a sheet's last row: a column's tip and list reach it
LARGEST = 16 * 1024 * 1024  # bytes a part may unpack to: a sheet of about 8,000 people takes a few megabytes
# Every cell wraps its text at the top; a header's is bold.
STYLES = (f'<styleSheet xmlns="{MAIN}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
          '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
          '<fills count="2"><fill><patternFill patternType="none"/></fill>'
          '<fill><patternFill patternType="gray125"/></fill></fills>'
          '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
          '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
          '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
          '<alignment vertical="top" wrapText="1"/></xf>'
          '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1">'
          '<alignment vertical="top" wrapText="1"/></xf></cellXfs>'
          '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
TEXT, HEADER = 1, 2  # the styles a cell takes
HIDDEN = ' state="hidden"'
LIST = ' type="list" errorStyle="stop" showErrorMessage="1"'  # a value off the list is refused


def write(sheets):
    """A workbook's bytes from its sheets, in order. Each is a dict: its "name" and its "rows" of texts from column A,
    a blank one left out; optionally its columns' "widths", whether it is "hidden", whether its first row is a "header",
    then bold and frozen, and the header's "tips": for each column, the input message its cells below show, and the
    range («Listas!$A$1:$A$9») their values must come from, or None for any value."""
    links = "".join(f'<Relationship Id="rId{n}" Type="{LINKS}/worksheet" Target="worksheets/sheet{n}.xml"/>'
                    for n in range(1, len(sheets) + 1))
    parts = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{PARTS}.sheet.main+xml"/>'
            f'<Override PartName="/xl/styles.xml" ContentType="{PARTS}.styles+xml"/>'
            + "".join(f'<Override PartName="/xl/worksheets/sheet{n}.xml" ContentType="{PARTS}.worksheet+xml"/>'
                      for n in range(1, len(sheets) + 1)) + "</Types>"),
        "_rels/.rels": relationships(f'<Relationship Id="rId1" Type="{LINKS}/officeDocument" '
                                     'Target="xl/workbook.xml"/>'),
        "xl/workbook.xml": (
            f'<workbook xmlns="{MAIN}" xmlns:r="{LINKS}"><sheets>'
            + "".join(f'<sheet name={quoteattr(sheet["name"])} sheetId="{n}"{HIDDEN if sheet.get("hidden") else ""} '
                      f'r:id="rId{n}"/>' for n, sheet in enumerate(sheets, 1)) + "</sheets></workbook>"),
        "xl/_rels/workbook.xml.rels": relationships(
            links + f'<Relationship Id="rId{len(sheets) + 1}" Type="{LINKS}/styles" Target="styles.xml"/>'),
        "xl/styles.xml": STYLES,
        **{f"xl/worksheets/sheet{n}.xml": worksheet(sheet) for n, sheet in enumerate(sheets, 1)}}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as package:
        for path, xml in parts.items():
            package.writestr(path, DECLARATION + xml)
    return out.getvalue()


def relationships(links):
    return f'<Relationships xmlns="{PACKAGE}">{links}</Relationships>'


def worksheet(sheet):
    """A sheet's part: its frozen header, its columns' widths, its cells and its header's tips."""
    header, tips = sheet.get("header"), sheet.get("tips", ())
    views = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
             'state="frozen"/></sheetView></sheetViews>' if header else "")
    widths = "".join(f'<col min="{n}" max="{n}" width="{width}" customWidth="1"/>'
                     for n, width in enumerate(sheet.get("widths", ()), 1))
    rows = "".join(
        f'<row r="{r}">' + "".join(
            f'<c r="{column(c)}{r}" s="{HEADER if header and r == 1 else TEXT}" t="inlineStr">'
            f"<is><t>{escape(text)}</t></is></c>" for c, text in enumerate(row) if text) + "</row>"
        for r, row in enumerate(sheet["rows"], 1) if any(row))
    rules = "".join(
        f'<dataValidation{LIST if values else ""} allowBlank="1" showInputMessage="1" promptTitle={quoteattr(title)} '
        f'prompt={quoteattr(tip)} sqref="{column(c)}2:{column(c)}{LAST_ROW}">'
        + (f"<formula1>{escape(values)}</formula1>" if values else "") + "</dataValidation>"
        for c, (title, (tip, values)) in enumerate(zip(sheet["rows"][0], tips)))
    return (f'<worksheet xmlns="{MAIN}">{views}' + (f"<cols>{widths}</cols>" if widths else "")
            + f"<sheetData>{rows}</sheetData>"
            + (f'<dataValidations count="{len(tips)}">{rules}</dataValidations>' if tips else "") + "</worksheet>")


def column(index):
    """A column's letter, from A for 0 to Z."""
    return chr(ord("A") + index)


def read(data, name):
    """The rows of the sheet called `name` in a workbook's bytes, each with its row number and its cells' values as text
    from column A to its last value, a blank cell empty: shared or inline strings, a formula's result and a number as
    saved, formatting ignored. ValueError for bytes that hold no workbook with that sheet."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            book = related(package, "", "officeDocument")[0]
            targets = relations(package, book)
            strings = [text(item) for part in related(package, book, "sharedStrings") for item in parse(package, part)]
            sheet = next(sheet for sheet in parse(package, book).iterfind("{*}sheets/{*}sheet")
                         if sheet.get("name") == name)
            part = targets[next(value for key, value in sheet.attrib.items() if key.endswith("}id"))][1]
            return list(rows(parse(package, part), strings))
    except Exception as error:  # whatever a package that is no such workbook throws
        raise ValueError(f"no workbook with a sheet called {name}") from error


def parse(package, part):
    """A part's XML, once its size shows it is no ZIP bomb."""
    if package.getinfo(part).file_size > LARGEST:
        raise ValueError(f"{part} unpacks to more than {LARGEST} bytes")
    return ElementTree.fromstring(package.read(part))


def relations(package, part):
    """A part's relationships by id, each with its type's last word and the part it points to."""
    folder, name = posixpath.split(part)
    return {link.get("Id"): (link.get("Type").rsplit("/", 1)[-1],
                             posixpath.normpath(posixpath.join("/", folder, link.get("Target"))).lstrip("/"))
            for link in parse(package, posixpath.join(folder, "_rels", f"{name}.rels"))}


def related(package, part, kind):
    """The parts a part points to with that type of relationship."""
    return [target for found, target in relations(package, part).values() if found == kind]


def text(item, path=""):
    """A string's text, its runs joined; a phonetic guide is no part of it."""
    return "".join(t.text or "" for t in item.findall(f"{path}{{*}}t") + item.findall(f"{path}{{*}}r/{{*}}t"))


def rows(sheet, strings):
    """Each row's number and its cells' values from column A to its last value."""
    number = 0
    for row in sheet.iterfind("{*}sheetData/{*}row"):
        number, values, index = int(row.get("r") or number + 1), {}, -1
        for cell in row.iterfind("{*}c"):
            index = position(cell.get("r")) if cell.get("r") else index + 1
            if found := value(cell, strings):
                values[index] = found
        yield [number, [values.get(index, "") for index in range(max(values, default=-1) + 1)]]


def value(cell, strings):
    """A cell's value as text: an inline or a shared string, or the value it saved."""
    kind, saved = cell.get("t"), cell.findtext("{*}v") or ""
    if kind == "inlineStr":
        return text(cell, "{*}is/")
    return strings[int(saved)] if kind == "s" and saved else saved


def position(reference):
    """The index of a cell reference's column, 0 for A1."""
    letters = reference.rstrip("0123456789")
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1
