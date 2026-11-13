# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The welcome page: every staff member lands on IntraVox («Inicio», a default fork app), whose home page names the
establishment and says where its shared folders are. Nextcloud's Dashboard, a second home with Nextcloud's own widgets
and voice, is off, so «Inicio» is the only one; without IntraVox, Nextcloud lands on Files. IntraVox keeps its pages in
the group folder Intranet, which grants every staff member what teams.SHARED declares, and no other group but
Nextcloud's administrators, as whom IntraVox's setup and occ reach the folder, and what an upgrade keeps of the grants
administrators added after «Listo» (`steps.kept`), but for IntraVox's own groups; IntraVox follows those grants, so
every staff member edits the pages. A page that is already there stays as staff left it. The step ensures IntraVox
and Groupfolders."""

import json

import steps
import teams
from steps import apps, differences
from steps.folders import grant, listed, reasons
from steps.people import ADMINS

NAME = "Página de bienvenida"
NEEDS = ("groups",)
APP = "intravox"
MOUNT = "Intranet"  # the group folder's name, as staff see it in Files
DASHBOARD = "dashboard"  # Nextcloud's Dashboard app, which the step turns off
IMPORTED = {"system": {"defaultapp": APP}, "apps": {APP: {"groupfolder_name": MOUNT}}}  # what occ config:import sets
SETTINGS = {**IMPORTED, "apps": {**IMPORTED["apps"], DASHBOARD: {"enabled": "no"}}}  # what Nextcloud holds once done
GREETING = "Bienvenida"  # the home page's first heading
HOME = "page-aps-00000001-0000-4000-8000-000000000001"
LINKS = (("Archivos", "Las carpetas del establecimiento", "/apps/files/", "folder"),
         ("Calendario", "La agenda del equipo", "/apps/calendar/", "calendar"),
         ("Contactos", "Teléfonos y correos", "/apps/contacts/", "contacts"),
         ("Talk", "Mensajes y videollamadas", "/apps/spreed/", "chat"))
GRANTS = {MOUNT: {**teams.SHARED, ADMINS: teams.MANAGE}}
KIND = "intranet_grants"  # in the record: the groups the step granted permissions in the Intranet
# IntraVox's own groups, which the suite never uses. IntraVox's first setup grants them the Intranet; the step takes
# back their grants whoever gave them, so an upgrade never keeps them as an administrator's.
INTRAVOX_GROUPS = ("IntraVox Admins", "IntraVox Editors", "IntraVox Users")
SETUP = ("intravox:setup", "--skip-demo")  # IntraVox's setup as its update hook runs it: folder and groups, no pages


def row(*widgets, **style):
    return {"columns": 1, **style, "widgets": [{"column": 1, "order": n, **w} for n, w in enumerate(widgets, 1)]}


def pages(centre):
    """The home page and the menu, by their path in the Intranet folder."""
    *units, last = teams.UNITS
    directorate = [teams.ROLES[role][0].format(type=centre["type"]) for role in teams.DIRECTORATE]
    folders = ("Todo lo compartido es de todo el personal: en «Archivos», usted lee, escribe, borra y comparte en la "
               f"carpeta de cada sector, de cada programa y de cada unidad ({', '.join(units)} y {last}), y en "
               f"{MOUNT}, donde puede editar esta página. Las carpetas {' y '.join(teams.PRIVATE)} son del equipo "
               f"directivo: solo las ven {', '.join(directorate[:-1])} y {directorate[-1]}.")
    home = {"uniqueId": HOME, "title": "Inicio", "language": "es", "layout": {"columns": 1, "sideColumns": [], "rows": [
        row({"type": "heading", "content": GREETING, "level": 3},
            {"type": "heading", "content": centre["name"], "level": 1},
            {"type": "text", "content": f"{centre['comuna']} · {centre['health_service']}"},
            backgroundColor="var(--color-primary-element)"),
        row({"type": "text", "content": folders}),
        row({"type": "links", "title": "", "layout": "tiles", "columns": len(LINKS), "items": [
            {"title": title, "text": text, "url": url, "icon": icon, "target": "_self"}
            for title, text, url, icon in LINKS]})]}}
    menu = {"type": "megamenu", "items": [{"id": "nav_inicio", "title": "Inicio", "uniqueId": HOME, "url": None,
                                           "target": None, "children": []}]}
    return {path: json.dumps(page, ensure_ascii=False, indent=1) + "\n"
            for path, page in (("es/home.json", home), ("es/navigation.json", menu))}


def run(context):
    if failed := apps.ensure(context, "groupfolders", APP):
        return failed
    suite = context.suite
    suite.occ("config:import", stdin=json.dumps(IMPORTED))  # IntraVox names its folder before it makes it
    suite.occ("app:disable", DASHBOARD)
    suite.occ(*SETUP)
    held = listed(suite)
    if MOUNT not in held:
        return reasons(context, KIND, GRANTS, app_groups=INTRAVOX_GROUPS)
    grant(context, KIND, held, GRANTS, app_groups=INTRAVOX_GROUPS)
    suite.seed(held[MOUNT]["id"], pages(context.centre))
    suite.occ("intravox:reindex")
    return steps.record(context, KIND, GRANTS, check(context))


def check(context):
    return differences(context.suite, SETTINGS) + reasons(context, KIND, GRANTS, app_groups=INTRAVOX_GROUPS)
