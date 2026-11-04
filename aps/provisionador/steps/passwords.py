# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The passwords file: the suite administrator's first password and AIO's passphrase, kept on the server readable by
root alone and handed over at «Listo»."""

from suite import write_root_only

NAME = "Archivo de contraseñas"
NEEDS = ("start",)
FILE = "contrasenas.txt"  # in the Provisionador's folder
MISSING = ("El archivo de contraseñas del servidor no está completo o pueden leerlo otros usuarios. Presione "
           "«Reintentar».")


def text(context):
    secrets = context.suite.secrets()
    return (f"APS Conecta Gestión: contraseñas de https://{context.name}\n"
            "Guarde este archivo en un lugar seguro y no lo comparta.\n\n"
            "Administrador de la suite\n"
            "  Usuario: admin\n"
            f"  Contraseña: {secrets['administrator']}\n\n"
            "Página de AIO, para las copias de seguridad y la restauración\n"
            f"  Frase de contraseña: {secrets['passphrase']}\n")


def run(context):
    write_root_only(context.folder / FILE, text(context))
    return check(context)


def check(context):
    kept = context.folder / FILE
    whole = kept.exists() and kept.stat().st_mode & 0o777 == 0o600 and kept.read_text(encoding="utf-8") == text(context)
    return [] if whole else [MISSING]
