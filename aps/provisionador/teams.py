# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The establishment's teams and what the install declares from them, in one place: the groups, the group folders with
each group's permissions, and the cargo accounts.

A team is a sector or a program the operator types on «Equipos», a shared folder with no member list. «Norte», «Sector
Norte» and «sector norte» are one team, Sector Norte: its id is sector-norte and its folder Sectores/Sector Norte.
«Cardiovascular» and «Programa Cardiovascular» are Programa Cardiovascular, prog-cardiovascular, in
Programas/Cardiovascular. Group ids are old gestion's; their names are Spanish, and a name that names the kind of
establishment names the centre's DEIS type (Director/a de SAPU). Groups do not nest: an account is in the staff-wide
group, its role's category and its role, and each folder grants groups.
"""

import re
import unicodedata

from deis import fold, hyphenated

STAFF = "all-staff"
JEFATURAS = "cat-jefaturas"
CATEGORIES = {JEFATURAS: "Jefaturas", "cat-clinicos": "Clínicos", "cat-tecnicos": "Técnicos",
              "cat-administrativos": "Administrativos"}
# The cargos every establishment shares, in category order, each with the category its holders also join; {type} is the
# centre's DEIS type.
ROLES = {
    "role-director-cesfam": ("Director/a de {type}", JEFATURAS),
    "role-subdirector-jefe-tecnico": ("Subdirector/a Médico o Jefe Técnico", JEFATURAS),
    "role-jefe-sector-mais": ("Jefe/a de Sector (Gestión MAIS)", JEFATURAS),
    "role-jefe-some": ("Jefe/a de SOME", JEFATURAS),
    "role-jefe-programa": ("Jefe/a de Programa", JEFATURAS),
    "role-medico-gestor": ("Médico/a Gestor/a", JEFATURAS),
    "role-jefe-apoyo-clinico": ("Jefe/a de Apoyo Clínico", JEFATURAS),
    "role-jefe-servicios-generales": ("Jefe/a de Servicios Generales", JEFATURAS),
    "role-medico": ("Médico General / de Familia", "cat-clinicos"),
    "role-dentista": ("Cirujano Dentista", "cat-clinicos"),
    "role-quimico-farmaceutico": ("Químico Farmacéutico (Dir. Técnico Farmacia)", "cat-clinicos"),
    "role-enfermeria": ("Enfermera/o", "cat-clinicos"),
    "role-matroneria": ("Matrona/Matrón", "cat-clinicos"),
    "role-kinesiologo": ("Kinesiólogo/a", "cat-clinicos"),
    "role-psicologo": ("Psicólogo/a", "cat-clinicos"),
    "role-trabajador-social": ("Trabajador/a Social", "cat-clinicos"),
    "role-nutricionista": ("Nutricionista", "cat-clinicos"),
    "role-terapeuta-ocupacional": ("Terapeuta Ocupacional", "cat-clinicos"),
    "role-fonoaudiologo": ("Fonoaudiólogo/a", "cat-clinicos"),
    "role-tecnologo-medico": ("Tecnólogo/a Médico/a", "cat-clinicos"),
    "role-educador-parvulos": ("Educador/a de Párvulos", "cat-clinicos"),
    "role-profesor-educacion-fisica": ("Profesor/a de Educación Física", "cat-clinicos"),
    "role-gestor-comunitario": ("Gestor/a Comunitario/a", "cat-clinicos"),
    "role-medico-oftalmologo": ("Médico/a Oftalmólogo/a", "cat-clinicos"),
    "role-tens-procedimientos": ("TENS – Procedimientos / Vacunatorio", "cat-tecnicos"),
    "role-tens-farmacia": ("TENS – Farmacia / PNAC", "cat-tecnicos"),
    "role-tons": ("TONS (Técnico en Odontología)", "cat-tecnicos"),
    "role-podologo": ("Podólogo/a", "cat-tecnicos"),
    "role-tecnico-rehabilitacion": ("Técnico/a en Rehabilitación", "cat-tecnicos"),
    "role-facilitador-intercultural": ("Facilitador/a Intercultural", "cat-tecnicos"),
    "role-tecnico-salud": ("Técnico/a de Salud", "cat-tecnicos"),
    "role-administrativo-some": ("Administrativo SOME", "cat-administrativos"),
    "role-oirs": ("Encargado/a OIRS", "cat-administrativos"),
    "role-estadistica-rem": ("Encargado/a de Estadística (REM)", "cat-administrativos"),
    "role-conductor": ("Conductor (Ambulancia / Traslado)", "cat-administrativos"),
    "role-auxiliar-servicio": ("Auxiliar de Servicio", "cat-administrativos"),
    "role-informatico": ("Informático/a (Soporte TI)", "cat-administrativos"),
    "role-recursos-humanos": ("Recursos Humanos", "cat-administrativos"),
    "role-abastecimiento-finanzas": ("Abastecimiento y Finanzas", "cat-administrativos"),
    "role-secretario-direccion": ("Secretario/a de Dirección", "cat-administrativos"),
    "role-guardia-seguridad": ("Guardia de Seguridad", "cat-administrativos"),
}
# By kind: the team id's prefix, the word the team's name starts with and the folder its folders sit in.
KINDS = {"sector": ("sector-", "Sector", "Sectores"), "program": ("prog-", "Programa", "Programas")}
LONGEST = 60  # characters in a typed name
# Groupfolders' permission words: managing a folder reads, writes, deletes and shares in it.
MANAGE = ("read", "write", "delete", "share")
BITS = {"read": 1, "write": 6, "delete": 8, "share": 16}  # each word's permissions, as Groupfolders lists them
SHARED = {STAFF: MANAGE}  # a shared folder's grants: every staff member manages it
UNITS = ("SOME", "Farmacia", "Dental", "OIRS", "Estadística-REM")  # the units whose folders are shared
PRIVATE = ("Dirección", "Subdirección")  # the directorate's units, whose folders only its cargos manage and see
DIRECTORATE = dict.fromkeys(("role-director-cesfam", "role-subdirector-jefe-tecnico", "role-secretario-direccion"),
                            MANAGE)
# The positions every establishment has: username, name and role.
POSITIONS = (("director", "Director/a de {type}", "role-director-cesfam"),
             ("subdirector", "Subdirector/a Médico o Jefe Técnico", "role-subdirector-jefe-tecnico"),
             ("jefe.farmacia", "Jefe/a de Farmacia (Químico/a Farmacéutico/a)", "role-quimico-farmaceutico"),
             ("jefe.some", "Jefe/a de SOME", "role-jefe-some"))
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


def groups(centre_type):
    """Every group the install creates, by id, with its name: the staff-wide group, the categories and the roles."""
    return {STAFF: "Todo el personal", **CATEGORIES,
            **{role: name.format(type=centre_type) for role, (name, _) in ROLES.items()}}


def bits(words):
    return sum(BITS[word] for word in words)


def folders(teams):
    """Every group folder but the Intranet, by its mount point, with each group's permissions in it: every staff member
    manages each shared unit's and each team's folder; only the directorate's cargos manage Dirección and Subdirección,
    which nobody else sees."""
    return {**{f"Unidades/{unit}": SHARED for unit in UNITS}, **{f"Unidades/{unit}": DIRECTORATE for unit in PRIVATE},
            **{team["folder"]: SHARED for team in teams}}


def cargo_accounts(teams, centre_type):
    """The cargo accounts, each with its username, name and groups: one per position every establishment has and a
    jefatura per sector. A sector's jefatura is jefe.<the sector> (jefe.norte), or jefe.<its team id>
    (jefe.sector-farmacia) when a position holds that username."""
    accounts = {user: {"user": user, "name": name.format(type=centre_type), "groups": [role, JEFATURAS, STAFF]}
                for user, name, role in POSITIONS}
    for team in teams:
        if team["kind"] == "sector":
            user = "jefe." + team["id"].removeprefix(KINDS["sector"][0])
            if user in accounts:
                user = "jefe." + team["id"]
            accounts[user] = {"user": user, "name": f"Jefe/a de {team['name']}",
                              "groups": ["role-jefe-sector-mais", JEFATURAS, STAFF]}
    return list(accounts.values())
