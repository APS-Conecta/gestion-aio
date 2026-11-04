# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The people spreadsheet the operator uploads on «Equipos», as a spreadsheet saves it as CSV with «;»: a first line
naming the columns, then one person per line. A person's groups are ids teams.py gives (sector-norte, role-medico),
separated by spaces or commas; exactly one person is the first administrator, «sí» in primer_admin. The suite's
administrator and the cargo accounts keep their usernames. Each error names its line, and a spreadsheet with any error
loads nobody.
"""

import csv
import io
import re
import unicodedata

import teams
from deis import fold

COLUMNS = ("usuario", "nombre", "apellidos", "correo", "grupos", "primer_admin")
FILE = "planilla-personal.csv"
TEMPLATE = "\ufeff" + ";".join(COLUMNS) + "\r\n"  # the byte order mark has Excel read it as UTF-8
ADMINISTRATOR = "admin"  # the suite's administrator, whom AIO creates
USER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
EMAIL = re.compile(r"\w[\w.+'-]*@[\w-]+(\.[\w-]+)+")
SEPARATOR = re.compile(r"[\s,]+")
LONGEST = 64  # characters in a display name, as Nextcloud keeps it
FIRST_ADMIN = {"si": True, "no": False, "": False}  # primer_admin's answers, accent- and case-blind

HEADER = "Línea 1: debe nombrar las columnas {}, separadas por «;». Descargue la planilla y vuelva a completarla."
WIDTH = "Línea {}: tiene {} columnas y debe tener {}, separadas por «;»."
SPANS = "Línea {}: una celda ocupa más de una línea. Quite sus saltos de línea."
BAD_USER = ("Línea {}: el usuario «{}» no es válido. Use hasta 64 caracteres entre letras minúsculas sin tildes, "
            "números, puntos y guiones, y comience con una letra o un número.")
REPEATED = "Línea {}: el usuario «{}» ya está en la línea {}."
RESERVED = "Línea {}: el usuario «{}» está reservado para {}."
NO_NAME = "Línea {}: faltan el nombre o los apellidos."
NOT_TEXT = "Línea {}: el nombre tiene caracteres que no son texto."
TOO_LONG = "Línea {}: «{}» es demasiado largo: el nombre y los apellidos deben sumar a lo más {} caracteres."
BAD_EMAIL = "Línea {}: el correo «{}» no es válido."
UNKNOWN = "Línea {}: el grupo «{}» no existe. Use los grupos que muestra «Equipos»."
BAD_FIRST_ADMIN = "Línea {}: primer_admin dice «{}». Escriba «sí» o déjelo vacío."
SECOND_ADMIN = "Línea {}: la línea {} ya marca al primer administrador. Marque solo una persona."
NO_ADMIN = "Ninguna persona está marcada como primer administrador. Escriba «sí» en primer_admin para una sola."
EMPTY = "La planilla no tiene personas. Agregue una por línea, bajo la que nombra las columnas."


def read(text, declared, centre_type):
    """The people the spreadsheet's text loads, each with its username, display name, email, the groups its line lists
    and whether it is the first administrator, and its errors in Spanish: nobody while it has one. No text loads
    nobody."""
    if not text:
        return [], []
    known = teams.groups(declared, centre_type)
    reserved = {ADMINISTRATOR: "el administrador de la suite",
                **{account["user"]: f"la cuenta de cargo «{account['name']}»"
                   for account in teams.cargo_accounts(declared, centre_type)}}
    rows = csv.reader(io.StringIO(text.removeprefix("\ufeff"), newline=""), delimiter=";")
    people, errors, seen, admins, end, lines = [], [], {}, [], 0, 0
    for row in rows:
        line, end = end + 1, rows.line_num
        cells = [cell.strip() for cell in row]
        while len(cells) > len(COLUMNS) and not cells[-1]:
            cells.pop()  # the empty columns a sheet may carry
        if line == 1:
            if [cell.lower() for cell in cells] != list(COLUMNS):
                return [], [HEADER.format(";".join(COLUMNS))]
            continue
        if not any(cells):
            continue  # a line with no data
        lines += 1
        if end != line:
            errors.append(SPANS.format(line))
        elif len(cells) != len(COLUMNS):
            errors.append(WIDTH.format(line, len(cells), len(COLUMNS)))
        else:
            found, wrong = person(line, cells, known, reserved, seen, admins)
            people += [found] if found else []
            errors += wrong
    if not end:
        errors.append(HEADER.format(";".join(COLUMNS)))
    elif not lines:
        errors.append(EMPTY)
    elif not admins:
        errors.append(NO_ADMIN)
    return ([] if errors else people), errors


def person(line, cells, known, reserved, seen, admins):
    """The person a line with every column loads, or None, and its errors. `seen` and `admins` gather the usernames and
    the first administrators' lines so far."""
    user, first, last, email, groups, admin = cells
    user, email = user.lower(), email.lower()
    name = " ".join(unicodedata.normalize("NFC", f"{first} {last}").split())
    listed = list(dict.fromkeys(group.lower() for group in SEPARATOR.split(groups) if group))
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
    errors += [UNKNOWN.format(line, group) for group in listed if group not in known]
    if fold(admin) not in FIRST_ADMIN:
        errors.append(BAD_FIRST_ADMIN.format(line, admin))
    elif FIRST_ADMIN[fold(admin)]:
        errors += [SECOND_ADMIN.format(line, admins[0])] if admins else []
        admins.append(line)
    return None if errors else {"user": user, "name": name, "email": email, "groups": listed,
                                "admin": FIRST_ADMIN[fold(admin)]}, errors
