# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The establishment's teams and what the install declares from them, in one place: the groups, the group folders with
each group's permissions, and the cargo accounts, whose usernames are reserved.

A team is a sector or a program the operator types on «Equipos». «Norte», «Sector Norte» and «sector norte» are one
team, Sector Norte: its group is sector-norte and its folder Sectores/Sector Norte. «Cardiovascular» and «Programa
Cardiovascular» are Programa Cardiovascular, prog-cardiovascular, in Programas/Cardiovascular. Group ids are English and
their names Spanish. Groups do not nest: an account is in the staff-wide group, its role's category, its role and its
teams, and each folder grants groups.
"""

import re
import unicodedata

from deis import fold, hyphenated

STAFF = "all-staff"
JEFATURAS = "cat-jefaturas"
CATEGORIES = {JEFATURAS: "Jefaturas", "cat-clinicos": "Clínicos", "cat-tecnicos": "Técnicos",
              "cat-administrativos": "Administrativos"}
# The roles every establishment shares, each with the category its holders also join.
ROLES = {
    "role-director-cesfam": ("Director/a de CESFAM", JEFATURAS),
    "role-subdirector-jefe-tecnico": ("Subdirector/a Médico o Jefe Técnico", JEFATURAS),
    "role-jefe-sector-mais": ("Jefe/a de Sector (Gestión MAIS)", JEFATURAS),
    "role-medico": ("Médico General / de Familia", "cat-clinicos"),
    "role-dentista": ("Cirujano Dentista", "cat-clinicos"),
    "role-quimico-farmaceutico": ("Químico Farmacéutico (Dir. Técnico Farmacia)", "cat-clinicos"),
    "role-enfermeria": ("Enfermera/o", "cat-clinicos"),
    "role-matroneria": ("Matrona/Matrón", "cat-clinicos"),
    "role-kinesiologo": ("Kinesiólogo/a", "cat-clinicos"),
    "role-psicologo": ("Psicólogo/a", "cat-clinicos"),
    "role-trabajador-social": ("Trabajador/a Social", "cat-clinicos"),
    "role-nutricionista": ("Nutricionista", "cat-clinicos"),
    "role-terapeuta-fono": ("Terapeuta Ocupacional / Fonoaudiólogo/a", "cat-clinicos"),
    "role-tens-procedimientos": ("TENS – Procedimientos / Vacunatorio", "cat-tecnicos"),
    "role-tens-farmacia": ("TENS – Farmacia / PNAC", "cat-tecnicos"),
    "role-tons": ("TONS (Técnico en Odontología)", "cat-tecnicos"),
    "role-administrativo-some": ("Administrativo SOME", "cat-administrativos"),
    "role-jefe-some": ("Jefe/a de SOME", JEFATURAS),
    "role-oirs": ("Encargado/a OIRS", "cat-administrativos"),
    "role-estadistica-rem": ("Encargado/a de Estadística (REM)", "cat-administrativos"),
    "role-conductor": ("Conductor (Ambulancia / Traslado)", "cat-administrativos"),
    "role-auxiliar-servicio": ("Auxiliar de Servicio", "cat-administrativos"),
    "role-emergencia-breakglass": ("Acceso de Emergencia (Break-Glass)", "cat-clinicos"),
}
# By kind: the group id's prefix, the word the team's name starts with and the folder its folders sit in.
KINDS = {"sector": ("sector-", "Sector", "Sectores"), "program": ("prog-", "Programa", "Programas")}
LONGEST = 60  # characters in a typed name
# Groupfolders' permission words: «read» alone reads; managing also writes, deletes and shares.
READ, READ_SHARE, MANAGE = ("read",), ("read", "share"), ("read", "write", "delete", "share")
BITS = {"read": 1, "write": 6, "delete": 8, "share": 16}  # each word's permissions, as Groupfolders lists them
TRANSVERSAL = "Transversal"
SUBFOLDERS = ("Protocolos", "Flujogramas", "Documentación", "Registro de redes", "Actas de reuniones")
UNITS = {"SOME": ("role-administrativo-some",), "Farmacia": ("role-quimico-farmaceutico", "role-tens-farmacia"),
         "Dental": ("role-dentista", "role-tons"), "OIRS": ("role-oirs",),
         "Estadística-REM": ("role-estadistica-rem",), "Dirección": ()}  # each with the roles that manage it
# The positions every establishment has: username, name and role.
POSITIONS = (("director", "Director/a de CESFAM", "role-director-cesfam"),
             ("subdirector", "Subdirector/a Médico o Jefe Técnico", "role-subdirector-jefe-tecnico"),
             ("jefe.farmacia", "Jefe/a de Farmacia (Químico/a Farmacéutico/a)", "role-quimico-farmaceutico"),
             ("jefe.some", "Jefe/a de SOME", "role-jefe-some"))
ADMINISTRATOR = "admin"  # the suite administrator's account, AIO's
NOT_NAMES = "Escriba los sectores y los programas como listas de nombres."
NOT_TEXT = "«{}» tiene caracteres que no son texto."
TOO_LONG = "«{}» es demasiado largo: use a lo más {} caracteres."


def team(kind, typed):
    """The team a typed name becomes, or None when it names none; ValueError, in Spanish, for one that cannot be a
    name."""
    prefix, word, parent = KINDS[kind]
    name = " ".join(unicodedata.normalize("NFC", typed).split())
    if not name.isprintable():
        raise ValueError(NOT_TEXT.format(name))
    if len(name) > LONGEST:
        raise ValueError(TOO_LONG.format(name, LONGEST))
    if re.match(rf"{word.lower()}\b", fold(name)):
        name = name[len(word):].strip(" -:")
    name = name.replace("/", "-")  # a «/» would nest the team's folder
    if not hyphenated(name):
        return None
    full = f"{word} {name}"
    return {"id": hyphenated(prefix + name)[:64].rstrip("-"), "kind": kind, "name": full,
            "folder": f"{parent}/{full if kind == 'sector' else name}"}


def declare(sectors, programs):
    """The teams the typed sectors and programs become, sectors first, each once."""
    found = {}
    for kind, names in (("sector", sectors), ("program", programs)):
        if not (isinstance(names, list) and all(isinstance(name, str) for name in names)):
            raise ValueError(NOT_NAMES)
        for typed in names:
            if declared := team(kind, typed):
                found.setdefault(declared["id"], declared)
    return list(found.values())


def groups(teams):
    """Every group the install creates, by id, with its name: the staff-wide group, the categories, the roles and one
    per team."""
    return {STAFF: "Todo el personal", **CATEGORIES, **{role: name for role, (name, _) in ROLES.items()},
            **{team["id"]: team["name"] for team in teams}}


def bits(words):
    return sum(BITS[word] for word in words)


def folders(teams):
    """Every group folder, by its mount point, with each group's permissions in it: the staff read Transversal and the
    Jefaturas manage it; a unit's roles manage its folder, which the Jefaturas read and share, or manage when the unit
    has no role of its own; a team and the Jefaturas manage the team's folder."""
    declared = {TRANSVERSAL: {STAFF: READ, JEFATURAS: MANAGE}}
    for unit, roles in UNITS.items():
        declared[f"Unidades/{unit}"] = {**dict.fromkeys(roles, MANAGE), JEFATURAS: READ_SHARE if roles else MANAGE}
    for team in teams:
        declared[team["folder"]] = {team["id"]: MANAGE, JEFATURAS: MANAGE}
    return declared


def cargo_accounts(teams):
    """The cargo accounts, each with its username, name and groups: one per position every establishment has and a
    jefatura per sector, in its sector's team too. A sector's jefatura is jefe.<the sector>."""
    accounts = {user: {"user": user, "name": name, "groups": [role, JEFATURAS, STAFF]}
                for user, name, role in POSITIONS}
    for team in teams:
        if team["kind"] == "sector":
            user = "jefe." + team["id"].removeprefix(KINDS["sector"][0])
            accounts.setdefault(user, {"user": user, "name": f"Jefe/a de {team['name']}",
                                       "groups": ["role-jefe-sector-mais", team["id"], JEFATURAS, STAFF]})
    return list(accounts.values())


def reserved(teams):
    """The usernames no person may take: the suite administrator's and every cargo account's."""
    return {ADMINISTRATOR, *(account["user"] for account in cargo_accounts(teams))}
