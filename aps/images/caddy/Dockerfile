# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The thin Caddy image: upstream's community Caddy (szaimen/aio-caddy, which community-containers/caddy runs)
# with the acmedns DNS module, so the name service's credential gets the certificate through the DNS challenge
# and no port has to be open. Only the DNS-provider part of upstream's settings changes.
FROM ghcr.io/szaimen/aio-caddy:v4@sha256:2ad8fd52342834a82e03105887057673e30502910d0af48e60a5d748a536ba9a AS upstream

# Upstream's caddy rebuilt with acmedns added: the build fails unless its modules, packages and versions are
# exactly upstream's plus dns.providers.acmedns.
FROM caddy:2.11.7-builder-alpine@sha256:95a5e7e2692b5eb9545aadc5b90a39f6f509bee59c9508a493f83ee902eaef63 AS builder
COPY --from=upstream /usr/local/bin/caddy /upstream/caddy
RUN set -eux; \
    xcaddy build --output /usr/bin/caddy \
        --with github.com/porech/caddy-maxmind-geolocation@v1.0.3 \
        --with github.com/mholt/caddy-l4@v0.1.1 \
        --with github.com/caddy-dns/desec@v1.1.0 \
        --with github.com/caddy-dns/acmedns@v0.7.0; \
    /upstream/caddy list-modules --packages --versions | grep '^[a-z]' > /upstream/modules; \
    /usr/bin/caddy list-modules --packages --versions | grep '^[a-z]' > /modules; \
    grep '^dns\.providers\.acmedns ' /modules; \
    grep -v '^dns\.providers\.acmedns ' /modules | diff /upstream/modules -

# dns-provider.sed turns upstream's deSEC block in /start.sh into acmedns; the build fails if any deSEC line is left.
FROM upstream
COPY --from=builder /usr/bin/caddy /usr/local/bin/caddy
COPY dns-provider.sed /tmp/
USER root
RUN set -eux; \
    sed -i -f /tmp/dns-provider.sed /start.sh; \
    if grep -in desec /start.sh; then exit 1; fi; \
    grep -q 'dns acmedns' /start.sh; \
    grep -qF 'dir {$ACME_CA:' /start.sh; \
    rm /tmp/dns-provider.sed
USER www-data
