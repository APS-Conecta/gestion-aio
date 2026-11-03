#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The certificate proof, on a runner nothing can reach from outside: claims a CI name from the name service's
# CI deployment, runs the thin Caddy image standalone with that credential against Let's Encrypt's staging
# service, and checks that Caddy serves a staging certificate for the name. Once the name is claimed it writes
# code= and name= to $GITHUB_OUTPUT, so a later step releases the name whatever happens here.
# Usage: certificate.sh <caddy image@digest>
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
[ "$#" -eq 1 ] || { echo "usage: $0 <caddy image@digest>" >&2; exit 2; }
caddy=$1
staging=https://acme-staging-v02.api.letsencrypt.org/directory

mapfile -t codes < <(python3 -I aps/provisionador/deis.py slugs | jq -r 'keys[]')
code="${codes[(GITHUB_RUN_ID + GITHUB_RUN_ATTEMPT) % ${#codes[@]}]}"
claim="$(aps/name-service/names.sh ci claim "$code")" || { echo "::error::the CI name service refused the claim: $claim"; exit 1; }
ACMEDNS_PASSWORD="$(printf '%s' "$claim" | jq -r .password)"
echo "::add-mask::$ACMEDNS_PASSWORD"
NC_DOMAIN="$(printf '%s' "$claim" | jq -r .name)"
ACMEDNS_USERNAME="$(printf '%s' "$claim" | jq -r .username)"
ACMEDNS_SUBDOMAIN="$(printf '%s' "$claim" | jq -r .subdomain)"
ACMEDNS_SERVER_URL="$(printf '%s' "$claim" | jq -r .server_url)"
export NC_DOMAIN ACMEDNS_USERNAME ACMEDNS_PASSWORD ACMEDNS_SUBDOMAIN ACMEDNS_SERVER_URL
printf 'code=%s\nname=%s\n' "$code" "$NC_DOMAIN" >> "$GITHUB_OUTPUT"
echo "claimed $NC_DOMAIN"

# What upstream's start.sh waits for in the suite: Nextcloud's port 9001 and the admin's caddy folder. The
# image's own Caddyfile gets only the staging directory, so the image runs as shipped otherwise.
work="$(mktemp -d)"
mkdir -p "$work/nextcloud/admin/files/nextcloud-aio-caddy"
touch "$work/nextcloud/admin/files/nextcloud-aio-caddy/allowed-countries.txt"
docker run --rm --entrypoint cat "$caddy" /Caddyfile \
  | awk -v dir="$staging" '{ print } /issuer acme \{/ { print "            dir " dir }' > "$work/Caddyfile"
grep -qF "dir $staging" "$work/Caddyfile" || { echo "::error::the image's Caddyfile has no 'issuer acme' block for staging"; exit 1; }
chmod 666 "$work/Caddyfile"
docker network create aps-certificate > /dev/null
docker run -d --name nextcloud-aio-nextcloud --network aps-certificate --entrypoint nc "$caddy" -lk 9001 > /dev/null
docker run -d --name nextcloud-aio-caddy --network aps-certificate --publish 127.0.0.1:8443:443 \
  --env NC_DOMAIN --env APACHE_PORT=11000 \
  --env ACMEDNS_USERNAME --env ACMEDNS_PASSWORD --env ACMEDNS_SUBDOMAIN --env ACMEDNS_SERVER_URL \
  --volume "$work/Caddyfile:/Caddyfile" --volume "$work/nextcloud:/nextcloud:ro" --volume aps-caddy-data:/data \
  "$caddy" > /dev/null

for _ in $(seq 60); do
  served="$(openssl s_client -connect 127.0.0.1:8443 -servername "$NC_DOMAIN" < /dev/null 2> /dev/null \
    | openssl x509 -noout -issuer -ext subjectAltName 2> /dev/null || true)"
  grep -q '(STAGING)' <<< "$served" && break
  sleep 10
done
echo "$served"
if ! grep -q '(STAGING)' <<< "$served" || ! grep -qF "DNS:$NC_DOMAIN" <<< "$served"; then
  docker logs nextcloud-aio-caddy 2>&1 | tail -n 60
  echo "::error::Caddy did not serve a Let's Encrypt staging certificate for $NC_DOMAIN within 10 minutes"
  exit 1
fi
