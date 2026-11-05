# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The thin Nextcloud image: upstream v14.4.3's Nextcloud image without AIO's one-click 100-user cap,
# with AIO's notifications and admin section in Spanish, Nextcloud's maintenance window at night, an
# antivirus that deletes infected files and the theme's browser texts rebuilt once the apps are installed and updated.
# The cap is the block between upstream's "AIO one-click settings" markers in /entrypoint.sh and the
# one-click keys of the image's aio.config.php; the build fails if any of it is left. Its start no longer
# turns the support app back on, which the theme install step (aps/provisionador/steps/theme.py) turns off.
FROM ghcr.io/nextcloud-releases/aio-nextcloud:20261029_150718@sha256:4e955a9bd3e34d66dd2cd7a30ad0c21559848fa3b9d6304b065c5a1253dbd2ac

RUN set -eux; \
    sed -i '/^# AIO one-click settings start/,/^# AIO one-click settings end/d' /entrypoint.sh; \
    sed -i '/one-click-instance/d' /usr/src/nextcloud/config/aio.config.php; \
    sed -i '/^php \/var\/www\/html\/occ app:enable support$/d' /entrypoint.sh; \
    if grep -n -e one-click-instance -e 'app:enable support' /entrypoint.sh \
        /usr/src/nextcloud/config/aio.config.php; then exit 1; fi

# AIO's texts in this container in Spanish, naming «APS Conecta Gestión»: aio-texts.sed rewrites every /notify.sh
# call and hint in /entrypoint.sh and every heading and link of the AIO admin section; the build fails if any of
# those lines is left as upstream's.
COPY aio-texts.sed /tmp/
RUN set -eux; \
    admin=/usr/src/nextcloud/apps/nextcloud-aio/templates/admin.php; \
    { grep -e '/notify.sh "' -e 'CUSTOM_HINT="' /entrypoint.sh; grep -e '<h2>' -e '<a ' "$admin"; } > /tmp/upstream-lines; \
    sed -i -f /tmp/aio-texts.sed /entrypoint.sh "$admin"; \
    if grep -xFf /tmp/upstream-lines /entrypoint.sh "$admin"; then exit 1; fi; \
    rm /tmp/aio-texts.sed /tmp/upstream-lines

# The hour (UTC) Nextcloud's heavy daily jobs start, which upstream's entrypoint sets at every start; the jobs install
# step (aps/provisionador/steps/jobs.py) declares the same one, and aps/gates/maintenance_window.py holds them equal.
ENV NEXTCLOUD_MAINTENANCE_WINDOW=1

# The antivirus deletes what its background scan finds infected: upstream's start sets its infected-file action to
# only_log, and here to delete, as the antivirus install step (aps/provisionador/steps/antivirus.py) declares it; the
# build fails if upstream's line is left.
RUN set -eux; \
    sed -i 's/\( files_antivirus av_infected_action --value=\)"only_log"$/\1"delete"/' /entrypoint.sh; \
    grep -q ' files_antivirus av_infected_action --value="delete"$' /entrypoint.sh; \
    if grep -n only_log /entrypoint.sh; then exit 1; fi

# browser-texts.php (its header says what it builds) runs once upstream's start installed and updated the apps; the
# build fails unless it follows the start's last app install or update.
COPY browser-texts.php /
RUN set -eux; \
    sed -i 's|^# Remove the update skip file always$|php /browser-texts.php\n&|' /entrypoint.sh; \
    last="$(grep -e 'occ app:update' -e 'occ app:install' -e '^php /browser-texts.php$' /entrypoint.sh | tail -n 1)"; \
    [ "$last" = 'php /browser-texts.php' ]
