#!/usr/bin/env bash
# Put a self-signed certificate where certbot keeps the real one, so nginx can start
# before certbot has ever run. Usage: ops/nginx/self-signed-cert.sh <domain>
# Compose finds its variables as usual: in .env, or in COMPOSE_ENV_FILES=ops/rehearsal.env.
set -euo pipefail

domain=${1:?usage: ops/nginx/self-signed-cert.sh <domain>}
live=/etc/letsencrypt/live/$domain

cd "$(dirname "$0")/../.."
docker compose -f compose.prod.yaml run --rm --no-deps --entrypoint sh certbot -c "
  mkdir -p '$live' &&
  openssl req -x509 -nodes -newkey rsa:2048 -days 30 -quiet \
    -keyout '$live/privkey.pem' -out '$live/fullchain.pem' \
    -subj '/CN=$domain' -addext 'subjectAltName=DNS:$domain,DNS:in.$domain'"
echo "Self-signed certificate for $domain and in.$domain is in place"
