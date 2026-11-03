# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The antivirus as AIO sets it up with its switch: its ClamAV container and the files_antivirus app, which scans every
upload and deletes an infected one. What its background scan finds infected later is deleted too: its infected-file
action is delete instead of AIO's only_log. Our thin Nextcloud image (aps/images/nextcloud) has AIO's start set the
same action, so a restart keeps it."""

from steps import configure, differences
from steps.apps import enabled

NAME = "Antivirus"
NEEDS = ("start",)
APP = "files_antivirus"
SETTINGS = {"apps": {APP: {"av_infected_action": "delete"}}}
OFF = ("AIO desactivó el antivirus porque ClamAV no respondió a tiempo cuando se inició Nextcloud. Contacte a APS "
       "Conecta.")


def run(context):
    configure(context.suite, SETTINGS)
    return check(context)


def check(context):
    return [OFF] if APP not in enabled(context.suite) else differences(context.suite, SETTINGS)
