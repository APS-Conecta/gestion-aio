# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The DEIS register: the public primary-care establishments, one of which is this install's centre.

The register is aps/deis/establecimientos-deis-<date>.csv; the date in its name is its provenance.
"""

import csv
import unicodedata
from pathlib import Path

# The screen's orders: regions north to south, and each type with the long name the list shows.
REGION_ORDER = ("15", "01", "02", "03", "04", "05", "13", "06", "07", "16", "08", "09", "14", "10", "11", "12")
TYPES = {
    "CESFAM": "Centro de Salud Familiar",
    "CECOSF": "Centro Comunitario de Salud Familiar",
    "PSR": "Posta de Salud Rural",
    "SAPU": "Servicio de Atención Primaria de Urgencia",
    "SUR": "Servicio de Urgencia Rural",
    "SAR": "Servicio de Alta Resolutividad",
    "COSAM": "Centro Comunitario de Salud Mental",
    "CGU": "Consultorio General Urbano",
    "CGR": "Consultorio General Rural",
}
SEARCH_LIMIT = 50


def fold(text):
    """Accent- and case-blind, so «castanos» finds «Castaños»."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))


def newest(directory):
    files = sorted(Path(directory).glob("establecimientos-deis-*.csv"))
    if not files:
        raise SystemExit(f"No se encontró el registro DEIS en {directory}. Descargue el instalador nuevamente.")
    return files[-1]


def timezone(row):
    """Isla de Pascua and Magallanes keep their own time; the rest of the country, continental time."""
    if row["comuna_codigo"] == "05201":  # Isla de Pascua
        return "Pacific/Easter"
    if row["region_codigo"] == "12":  # Magallanes y de la Antártica Chilena
        return "America/Punta_Arenas"
    return "America/Santiago"


def point(value):
    """A map point off the wire, {"latitude", "longitude"} in decimal degrees, or None when it is not one."""
    if not (isinstance(value, dict) and value.keys() == {"latitude", "longitude"}):
        return None
    lat, lon = value["latitude"], value["longitude"]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lon)):
        return None
    return {"latitude": float(lat), "longitude": float(lon)} if abs(lat) <= 90 and abs(lon) <= 180 else None


def establishment(row):
    """A register row as the search answers it."""
    return {"code": row["codigo"], "type": row["tipo"], "name": row["nombre"], "address": row["direccion"],
            "comuna": row["comuna"], "region": row["region"],
            "point": {"latitude": float(row["latitud"]), "longitude": float(row["longitud"])}}


def centre(row, moved=None):
    """The centre the install stores: the establishment's identity, its official point, the point the
    operator confirmed and the timezone."""
    found = establishment(row)
    return {**found, "comuna_code": row["comuna_codigo"], "region_code": row["region_codigo"],
            "health_service": row["servicio_salud"], "dependency": row["dependencia"],
            "official_point": found["point"], "point": moved or found["point"], "timezone": timezone(row)}


class Register:
    def __init__(self, path):
        path = Path(path)
        self.date = path.stem.rsplit("-deis-", 1)[-1]
        with open(path, encoding="utf-8", newline="") as fh:
            self.rows = sorted(csv.DictReader(fh), key=lambda r: fold(r["nombre"]))
        self.by_code = {r["codigo"]: r for r in self.rows}

    def facets(self):
        """The regions, comunas and types the search filters offer."""
        regions = {r["region_codigo"]: r["region"] for r in self.rows}
        comunas = {r["comuna_codigo"]: (r["comuna"], r["region_codigo"]) for r in self.rows}
        types = {r["tipo"] for r in self.rows}
        return {
            "date": self.date,
            "regions": [{"code": c, "name": regions[c]} for c in REGION_ORDER if c in regions],
            "comunas": sorted(({"code": c, "name": n, "region": r} for c, (n, r) in comunas.items()),
                              key=lambda x: fold(x["name"])),
            "types": [{"code": t, "name": name} for t, name in TYPES.items() if t in types],
        }

    def search(self, region="", comuna="", type="", name=""):
        """The rows matching every filter given; a name matches when it holds every word typed."""
        words = fold(name).split()
        found = [r for r in self.rows
                 if region in ("", r["region_codigo"]) and comuna in ("", r["comuna_codigo"])
                 and type in ("", r["tipo"]) and all(w in fold(r["nombre"]) for w in words)]
        return {"total": len(found), "rows": [establishment(r) for r in found[:SEARCH_LIMIT]]}
