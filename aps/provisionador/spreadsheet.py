# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The people spreadsheet the operator downloads on «Equipos» as an Excel workbook built for the centre, fills in and
uploads, as Excel or as CSV with «;»: a first row naming the columns, then one person per row. A person's cargo is blank
or one of the cargo names teams.py gives; exactly one person is the first administrator, «sí» in «Primer
administrador», and not one retired with «Retirar». Headers, cargos and «sí» are read without accents or capitals. The
suite's administrator and the cargo accounts keep their usernames. Both formats become the same rows, each numbered by
its line; each error names its line, and a spreadsheet with any error loads nobody.
"""

import csv
import io
import itertools
import re
import unicodedata

import teams
import workbook
from deis import fold

# Each column of «Personal»: its header, whether it is required, what it takes, which its cells' tip shows, and an
# example. «Instrucciones» explains them.
GUIDE = (
    ("Usuario", "Sí", "Con él la persona entra a la suite: hasta 64 letras minúsculas sin tildes, números, puntos y "
                      "guiones, comenzando con una letra o un número. No puede repetirse ni ser «admin» o el usuario "
                      "de una cuenta de cargo.", "ana.rojas"),
    ("Nombre", "Sí", "Su nombre o sus nombres. Con los apellidos, a lo más 64 caracteres.", "Ana María"),
    ("Apellidos", "Sí", "Sus apellidos.", "Rojas Núñez"),
    ("Correo", "No", "Su correo electrónico.", "ana.rojas@example.org"),
    ("Cargo", "No", "Uno de la lista de la celda. Si el suyo no está, déjelo vacío: la persona tendrá igual su cuenta, "
                    "en «Todo el personal».", "Enfermera/o"),
    ("Primer administrador", "En una persona", "«sí» para la persona que administrará la suite, y solo para ella; "
                                               "«no» o vacío para las demás.", "sí"),
)
COLUMNS = tuple(column for column, *_ in GUIDE)
HOW = (
    ("Cómo completarla", "", "Escriba una persona por fila en la hoja «Personal», desde la fila 2, sin cambiar la "
                             "primera. Al seleccionar una celda, verá qué acepta su columna."),
    ("Cómo guardarla", "", "Guárdela como libro de Excel (.xlsx); en Google Sheets, con Archivo > Descargar > "
                           "Microsoft Excel (.xlsx). También puede guardar la hoja «Personal» como CSV delimitado por "
                           "«;»."),
    ("Cómo subirla", "", "En «Equipos», presione «Subir la planilla completa» y elija el archivo. Si tiene errores, la "
                         "página los nombra por línea: corríjalos y súbala de nuevo."),
)
FILE = "planilla-personal.xlsx"
PERSONAL, LISTS = "Personal", "Listas"
ANSWERS = ("sí", "no")  # «Primer administrador»'s list
ZIP = b"PK\x03\x04"  # how a workbook's bytes begin
ADMINISTRATOR = "admin"  # the suite's administrator, whom AIO creates
USER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
EMAIL = re.compile(r"\w[\w.+'-]*@[\w-]+(\.[\w-]+)+")
LONGEST = 64  # characters in a display name, as Nextcloud keeps it
FIRST_ADMIN = {"si": True, "no": False, "": False}  # «Primer administrador»'s answers, accent- and case-blind

HEADER = ("Línea 1: debe nombrar las columnas {}, en un CSV separadas por «;». Descargue la planilla y vuelva a "
          "completarla.")
WIDTH = "Línea {}: tiene {} columnas y debe tener {}, en un CSV separadas por «;»."
SPANS = "Línea {}: una celda ocupa más de una línea. Quite sus saltos de línea."
BAD_USER = ("Línea {}: el usuario «{}» no es válido. Use hasta 64 caracteres entre letras minúsculas sin tildes, "
            "números, puntos y guiones, y comience con una letra o un número.")
REPEATED = "Línea {}: el usuario «{}» ya está en la línea {}."
RESERVED = "Línea {}: el usuario «{}» está reservado para {}."
NO_NAME = "Línea {}: faltan el nombre o los apellidos."
NOT_TEXT = "Línea {}: el nombre tiene caracteres que no son texto."
TOO_LONG = "Línea {}: «{}» es demasiado largo: el nombre y los apellidos deben sumar a lo más {} caracteres."
BAD_EMAIL = "Línea {}: el correo «{}» no es válido."
NO_CARGO = "Línea {}: el cargo «{}» no está en la lista de cargos del establecimiento. Corríjalo o déjelo vacío."
BAD_FIRST_ADMIN = "Línea {}: «Primer administrador» dice «{}». Escriba «sí» o «no»."
SECOND_ADMIN = "Línea {}: la línea {} ya marca al primer administrador. Marque solo una persona."
RETIRED_ADMIN = "Línea {}: «{}» fue retirado/a y no puede ser el primer administrador. Marque a otra persona."
NO_ADMIN = ("Ninguna persona está marcada como primer administrador. Escriba «sí» en «Primer administrador» para una "
            "sola.")
EMPTY = "La planilla no tiene personas. Agregue una por línea, bajo la que nombra las columnas."


def template(centre_type):
    """The blank workbook for a centre of that type: «Instrucciones» first, explaining each column with an example, then
    «Personal» with the headers, whose Cargo and Primer administrador cells take a value of the hidden «Listas»: the
    cargo names in category order, and «sí» and «no»."""
    cargos = [teams.groups(centre_type)[role] for role in teams.ROLES]
    lists = {"Cargo": f"{LISTS}!$A$1:$A${len(cargos)}", "Primer administrador": f"{LISTS}!$B$1:$B${len(ANSWERS)}"}
    return workbook.write([
        {"name": "Instrucciones", "header": True, "widths": (22, 16, 80, 24),
         "rows": [("Columna", "Obligatoria", "Qué acepta", "Ejemplo"), *GUIDE, (), *HOW]},
        {"name": PERSONAL, "header": True, "widths": (18, 20, 24, 30, 44, 22), "rows": [COLUMNS],
         "tips": [(takes, lists.get(column)) for column, _, takes, _ in GUIDE]},
        {"name": LISTS, "hidden": True, "rows": list(itertools.zip_longest(cargos, ANSWERS, fillvalue=""))}])


def rows(data):
    """The rows of an uploaded spreadsheet, each with its line number and its cells: the «Personal» sheet of a workbook,
    which a ZIP container is, or the lines of a CSV, as UTF-8 or else Windows-1252, as Excel saves a plain CSV.
    ValueError for bytes that are neither."""
    if data.startswith(ZIP):
        return [[line, cells + [""] * (len(COLUMNS) - len(cells))] for line, cells in workbook.read(data, PERSONAL)]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    lines, found, end = csv.reader(io.StringIO(text.removeprefix("\ufeff"), newline=""), delimiter=";"), [], 0
    try:
        for cells in lines:
            found.append([end + 1, cells])
            end = lines.line_num
    except csv.Error as error:  # a cell past the csv module's limit, as a quote left open makes
        raise ValueError(f"line {end + 1}: {error}") from error
    return found


def read(rows, declared, centre_type, retired=()):
    """The people the spreadsheet's rows load, each with its username, display name, email, the group id of its cargo
    (None when blank) and whether it is the first administrator, and its errors in Spanish: nobody while it has one. No
    spreadsheet (None) loads nobody. `retired` holds the usernames retired with «Retirar»."""
    if rows is None:
        return [], []
    if not rows or rows[0][0] != 1 or [fold(cell) for cell in trimmed(rows[0][1])] != [fold(c) for c in COLUMNS]:
        return [], [HEADER.format(", ".join(f"«{column}»" for column in COLUMNS))]
    cargos = {fold(name): role for role, name in teams.groups(centre_type).items() if role in teams.ROLES}
    reserved = {ADMINISTRATOR: "el administrador de la suite",
                **{account["user"]: f"la cuenta de cargo «{account['name']}»"
                   for account in teams.cargo_accounts(declared, centre_type)}}
    people, errors, seen, admins, lines = [], [], {}, [], 0
    for line, row in rows[1:]:
        cells = trimmed(row)
        if not any(cells):
            continue  # a line with no data
        lines += 1
        if any("\n" in cell or "\r" in cell for cell in cells):
            errors.append(SPANS.format(line))
        elif len(cells) != len(COLUMNS):
            errors.append(WIDTH.format(line, len(cells), len(COLUMNS)))
        else:
            found, wrong = person(line, cells, cargos, reserved, retired, seen, admins)
            people += [found] if found else []
            errors += wrong
    if not lines:
        errors.append(EMPTY)
    elif not admins:
        errors.append(NO_ADMIN)
    return ([] if errors else people), errors


def trimmed(row):
    """A row's cells without surrounding spaces nor the empty columns a sheet may carry past the last."""
    cells = [cell.strip() for cell in row]
    while len(cells) > len(COLUMNS) and not cells[-1]:
        cells.pop()
    return cells


def person(line, cells, cargos, reserved, retired, seen, admins):
    """The person a line with every column loads, or None, and its errors. `cargos` holds each cargo's group id by its
    folded name; `seen` and `admins` gather the usernames and the first administrators' lines so far."""
    user, first, last, email, cargo, admin = cells
    user, email = user.lower(), email.lower()
    name = " ".join(unicodedata.normalize("NFC", f"{first} {last}").split())
    errors = []
    if not USER.fullmatch(user):
        errors.append(BAD_USER.format(line, user))
    elif user in seen:
        errors.append(REPEATED.format(line, user, seen[user]))
    elif user in reserved:
        errors.append(RESERVED.format(line, user, reserved[user]))
    else:
        seen[user] = line
    if not (first and last):
        errors.append(NO_NAME.format(line))
    elif not name.isprintable():
        errors.append(NOT_TEXT.format(line))
    elif len(name) > LONGEST:
        errors.append(TOO_LONG.format(line, name, LONGEST))
    if email and not EMAIL.fullmatch(email):
        errors.append(BAD_EMAIL.format(line, email))
    if cargo and fold(cargo) not in cargos:
        errors.append(NO_CARGO.format(line, cargo))
    if fold(admin) not in FIRST_ADMIN:
        errors.append(BAD_FIRST_ADMIN.format(line, admin))
    elif FIRST_ADMIN[fold(admin)]:
        errors += [SECOND_ADMIN.format(line, admins[0])] if admins else []
        errors += [RETIRED_ADMIN.format(line, user)] if user in retired else []
        admins.append(line)
    return None if errors else {"user": user, "name": name, "email": email, "cargo": cargos.get(fold(cargo)),
                                "admin": FIRST_ADMIN[fold(admin)]}, errors
