# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The DNS-provider part of upstream's /start.sh, deSEC's block made acmedns: the name service's credential in
# ACMEDNS_USERNAME, ACMEDNS_PASSWORD, ACMEDNS_SUBDOMAIN and ACMEDNS_SERVER_URL, and the ACME directory in ACME_CA
# (Let's Encrypt's production one when unset). The test expands ${ACMEDNS_PASSWORD:+set}, not the password, so the
# script's set -x never logs it.
s|to the deSEC DNS-01 challenge\.$|to the DNS-01 challenge of the APS name service, through acme-dns.|
s|"dns desec"|"dns acmedns"|
s|\[ -n "\$DESEC_TOKEN" \]|[ -n "${ACMEDNS_PASSWORD:+set}" ]|
s|print "            dns desec {"; print "                token {env\.DESEC_TOKEN}"; print "            }";|print "            dir {$ACME_CA:https://acme-v02.api.letsencrypt.org/directory}"; print "            dns acmedns {"; print "                username {$ACMEDNS_USERNAME}"; print "                password {env.ACMEDNS_PASSWORD}"; print "                subdomain {$ACMEDNS_SUBDOMAIN}"; print "                server_url {$ACMEDNS_SERVER_URL}"; print "            }";|
s|Failed to apply deSEC DNS challenge configuration|Failed to apply the acme-dns DNS challenge configuration|
