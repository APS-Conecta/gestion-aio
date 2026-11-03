# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The thin Nextcloud image: upstream v14.4.3's Nextcloud image without AIO's one-click 100-user cap and
# with AIO's notifications and admin section in Spanish.
# The cap is the block between upstream's "AIO one-click settings" markers in /entrypoint.sh and the
# one-click keys of the image's aio.config.php; the build fails if any of it is left.
FROM ghcr.io/nextcloud-releases/aio-nextcloud:20261029_150718@sha256:4e955a9bd3e34d66dd2cd7a30ad0c21559848fa3b9d6304b065c5a1253dbd2ac

RUN set -eux; \
    sed -i '/^# AIO one-click settings start/,/^# AIO one-click settings end/d' /entrypoint.sh; \
    sed -i '/one-click-instance/d' /usr/src/nextcloud/config/aio.config.php; \
    if grep -n one-click-instance /entrypoint.sh /usr/src/nextcloud/config/aio.config.php; then exit 1; fi

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
