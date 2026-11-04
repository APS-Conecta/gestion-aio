# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The passwords file: the suite administrator's first password, AIO's passphrase, the encryption password AIO made for
its backup, which a restore on another server asks for, and the first password of each account the install creates,
kept on the server readable by root alone and handed over at «Listo». A step that creates accounts asks `first` for
their passwords, which the file then lists. Without the backup's encryption password, no file is written."""

import json
from secrets import token_hex

from suite import write_root_only

NAME = "Archivo de contraseñas"
NEEDS = ("start",)
FILE = "contrasenas.txt"  # in the Provisionador's folder
ACCOUNTS = "first-passwords.json"  # beside it: each account's name and first password, by its username
MISSING = ("El archivo de contraseñas del servidor no está completo o pueden leerlo otros usuarios. Presione "
           "«Reintentar».")


def accounts(folder):
    """Each account the install made a first password for, by username: the name the file shows and the password."""
    kept = folder / ACCOUNTS
    return json.loads(kept.read_text(encoding="utf-8")) if kept.exists() else {}


def first(context, named):
    """The first password of each account in `named`, {username: the name the file shows}: made the first time an
    account is named and kept from then on, so a rerun or a new name never changes it. The passwords file lists it at
    once, under the account's current name."""
    kept = accounts(context.folder)
    new = {user: {"name": name, "password": kept[user]["password"] if user in kept else token_hex(12)}
           for user, name in named.items() if kept.get(user, {}).get("name") != name}
    if new:
        kept.update(new)
        write_root_only(context.folder / ACCOUNTS, json.dumps(kept, ensure_ascii=False, indent=1))
        write_root_only(context.folder / FILE, text(context))
    return {user: kept[user]["password"] for user in named}


def text(context):
    secrets = context.suite.secrets()
    return (f"APS Conecta Gestión: contraseñas de https://{context.name}\n"
            "Guarde este archivo en un lugar seguro y no lo comparta.\n\n"
            "Administrador de la suite\n"
            "  Usuario: admin\n"
            f"  Contraseña: {secrets['administrator']}\n\n"
            "Página de AIO, para las copias de seguridad y la restauración\n"
            f"  Frase de contraseña: {secrets['passphrase']}\n\n"
            "Copias de seguridad: contraseña necesaria para restaurar una copia en este u otro servidor\n"
            f"  Contraseña de cifrado: {secrets['backup_password']}\n"
            + "".join(f"\n{account['name']}\n  Usuario: {user}\n  Contraseña: {account['password']}\n"
                      for user, account in accounts(context.folder).items()))


def run(context):
    write_root_only(context.folder / FILE, text(context))
    return check(context)


def check(context):
    kept = context.folder / FILE
    whole = kept.exists() and kept.stat().st_mode & 0o777 == 0o600 and kept.read_text(encoding="utf-8") == text(context)
    return [] if whole else [MISSING]
