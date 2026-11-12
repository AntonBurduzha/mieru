---
id: T02
title: Production compose, nginx and TLS, rehearsed locally
mode: human
status: build
features: []
depends: [T01]
branch: t02-prod-compose-nginx
---

# T02 Production compose, nginx and TLS, rehearsed locally

## Goal
The production stack runs on the Mac from one file, `compose.prod.yaml`. nginx terminates TLS and routes by host and path, ingest stays reachable while one of its two replicas is down, only nginx publishes ports, every container has a memory limit, and no IP address or user agent reaches a log. T03 runs the same files on the server with a real certificate.

## Context to read
- `docs/architecture.md`: section 11 ("Containers", "Routing", "How nginx is set up", "Network and access"), section 5.1 step 5 (`X-Real-IP`), section 13 rows 19 and 21.
- `docs/conventions.md`: section 1 (layout), section 7.5 (headers, server and containers), section 7.7 row "Server and deploy".
- `docs/frontend.md` section 3: the dashboard CSP.
- `compose.yaml`: the database healthchecks to reuse. `ops/clickhouse/low-memory.xml`.
- `apps/backend/Dockerfile`: the `GIT_SHA` build arg and uid 10001. There's no curl in the image.
- `apps/backend/src/mieru/ingest/routes.py` and `api/routes/health.py`: `/health`, `/health/ready`, `/version`, `/api/v1/version`.

## Scope
- **In:**
  - `compose.prod.yaml`: nginx; certbot (profile `certbot`, never started by `up`); `ingest-1`, `ingest-2`, `api`; postgres, redis, clickhouse; the `edge` and `internal` networks; healthchecks, restart policies and memory limits.
  - The nginx config: routing, passive health checks and retries, `X-Real-IP`, access logs without IPs, security headers, the ACME webroot, and a default server that refuses unknown hosts.
  - A self-signed certificate script, the rehearsal variables, and a README section.
- **Out:**
  - T03: real certificates, the renewal timer, DNS, the provider firewall, Docker's log size cap.
  - T04: `ops/deploy.sh` and the rolling-restart script. T11: the `writer` and `worker` containers.
  - T20: serving the web app's files (`gzip_static`, `try_files`); until then `/` answers a placeholder.
  - T09: the `/js/p.js` headers. T26: the header and port-scan audit. No Python changes.
- **Files allowed to change:**
  - `compose.prod.yaml` (new);
  - `ops/nginx/**` (new): `nginx.conf`, `templates/mieru.conf.template`, `snippets/*.conf`, `self-signed-cert.sh`;
  - `ops/rehearsal.env` (new; dummy values for the Mac only);
  - `README.md`: one section, "Production stack, rehearsed locally";
  - `docs/conventions.md` section 1: drop "(later)" from `compose.prod.yaml` and `ops/nginx/`;
  - `docs/architecture.md`: section 11 and new section 13 rows for the decisions in Notes.

## Interfaces
- **Project:** `name: mieru-prod`, so it never shares containers or volumes with the dev project `mieru`.
- **Variables** (interpolated, each `${VAR:?}`): `MIERU_TAG`, `MIERU_DOMAIN` (the dashboard host; ingest is `in.${MIERU_DOMAIN}`), `POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD`, `ACME_EMAIL`. On the server they come from `/opt/mieru/.env`, on the Mac from `ops/rehearsal.env`:

  ```text
  MIERU_TAG=local
  MIERU_DOMAIN=localhost
  POSTGRES_PASSWORD=rehearsal
  CLICKHOUSE_PASSWORD=rehearsal
  ACME_EMAIL=rehearsal@example.com
  ```
- **App image:** `ghcr.io/antonburduzha/mieru-app:${MIERU_TAG}`. Settings come from an explicit `environment:` list: `ENV: prod`, the three DSNs built from the passwords with service names as hosts, `APP_BASE_URL: https://${MIERU_DOMAIN}`, `INGEST_BASE_URL: https://in.${MIERU_DOMAIN}`. No `env_file`, and no `GIT_SHA`: the image's value must win.
- **App commands:** `uvicorn --factory mieru.ingest.app:create_app --host 0.0.0.0 --port 8001 --no-proxy-headers` (api: `mieru.api.app:create_app`, port 8000). Healthchecks: `python -c` with `urllib.request` on `/health`.
- **Pinned images:** `nginx:1.30.6-alpine` and `certbot/certbot:v5.9.0`. Postgres, Redis and ClickHouse use the same tags as `compose.yaml`.
- **Redis:** the dev flags plus `--maxmemory 400mb --maxmemory-policy noeviction`.
- **Memory limits:**

  | nginx | ingest-1, ingest-2 | api | postgres | redis | clickhouse | certbot |
  |---|---|---|---|---|---|---|
  | 128m | 256m each | 384m | 512m | 512m | 2560m | 256m |
- **Networks:** `edge` (nginx and the apps) and `internal` with `internal: true` (the apps and the databases). certbot is on neither; it needs the internet to reach Let's Encrypt.
- **Volumes:** `letsencrypt` (certbot's `/etc/letsencrypt`) and `certbot-www` (the webroot `/var/www/certbot`), both mounted read-only in nginx. The certificate lives at `/etc/letsencrypt/live/${MIERU_DOMAIN}/{fullchain,privkey}.pem`.
- **`ops/nginx/self-signed-cert.sh <domain>`** writes a self-signed certificate for `<domain>` and `in.<domain>` to that path, with `openssl` from the certbot image (`docker compose -f compose.prod.yaml run --rm --no-deps --entrypoint sh certbot -c "mkdir -p ... && openssl req ..."`). It reads its variables like any compose command. On the Mac: `COMPOSE_ENV_FILES=ops/rehearsal.env ops/nginx/self-signed-cert.sh localhost`.
- **nginx** uses the official image's template step: `templates/mieru.conf.template` becomes `conf.d/mieru.conf` with `${MIERU_DOMAIN}` filled in. Every `server` and `location` that sets headers includes the snippets.
  - **Port 80:** `/.well-known/acme-challenge/` is served from the webroot. Everything else gets a `301` to `https://$host$request_uri`.
  - **The dashboard host:** `/api/` → upstream `api`; exact `/health` and `/health/ready` → `api`; `/` → `200` with the text `Mieru is coming soon`. An access log format without `$remote_addr` or `$http_user_agent`, and the CSP from `docs/frontend.md`.
  - **The ingest host:** every path → upstream `ingest`; `access_log off`; `Access-Control-Allow-Origin: *`.
  - **Both hosts:** the headers in conventions section 7.5, `server_tokens off`, `proxy_set_header X-Real-IP $remote_addr;` and `proxy_set_header X-Forwarded-For "";`.
  - **Upstreams:** a `zone`, `server <name>:<port> resolve max_fails=1 fail_timeout=5s`, and `resolver 127.0.0.11 valid=5s`. Proxying uses `proxy_next_upstream error timeout` and `proxy_connect_timeout 1s`.
  - **Error log:** `error_log stderr crit`. **Unknown hosts:** the default server answers `444` on port 80 and uses `ssl_reject_handshake on` on 443.
  - **Healthcheck:** `wget` on `http://127.0.0.1:8080/healthz`, a server that listens on loopback only and has its access log off.

## Acceptance criteria
Every check runs from the repo root, after these setup steps:

```bash
docker build -f apps/backend/Dockerfile --build-arg GIT_SHA=$(git rev-parse HEAD) -t ghcr.io/antonburduzha/mieru-app:local .
COMPOSE_ENV_FILES=ops/rehearsal.env ops/nginx/self-signed-cert.sh localhost
```

`DC` below stands for `docker compose -f compose.prod.yaml --env-file ops/rehearsal.env`. Write it out in full when you run a command.

1. The file is valid, and a missing tag stops it. (auto: `DC config --quiet && ! MIERU_TAG= DC config --quiet`)
2. Every service becomes healthy, and nginx's config tests clean. (auto: `DC up -d --wait && DC exec nginx nginx -t`)
3. Only nginx publishes ports, and the databases' network has no route out. (auto: `test "$(DC ps --format '{{.Service}} {{.Ports}}' | grep -- '->' | cut -d' ' -f1)" = nginx && test "$(docker network inspect mieru-prod_internal -f '{{.Internal}}')" = true`)
4. Every running container has a memory limit. (auto: `docker inspect -f '{{.Name}} {{.HostConfig.Memory}}' $(DC ps -q) | awk '$2 == 0 {bad = 1} END {exit bad}'`)
5. Plain HTTP redirects to HTTPS, but the ACME challenge path is served over HTTP. (auto: `test "$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' http://localhost/x)" = "301 https://localhost/x" && DC run --rm --entrypoint sh certbot -c 'mkdir -p /var/www/certbot/.well-known/acme-challenge && echo probe-ok > /var/www/certbot/.well-known/acme-challenge/probe' && test "$(curl -s http://localhost/.well-known/acme-challenge/probe)" = probe-ok`)
6. Routing reaches the right app, which runs the image's commit: `/api/v1/version` returns the HEAD sha, `/health/ready` on both hosts is all `ok`, and `/` shows the placeholder. (auto: `test "$(curl -sk https://localhost/api/v1/version)" = "{\"version\":\"$(git rev-parse HEAD)\"}" && curl -skf https://localhost/health/ready | grep -q '"clickhouse":"ok"' && curl -skf https://in.localhost/health/ready | grep -q '"redis":"ok"' && curl -skf https://localhost/ | grep -q 'Mieru is coming soon'`)
7. The headers are set on both hosts, including proxied responses. The dashboard host: HSTS with `includeSubDomains`, `nosniff`, a CSP with `frame-ancestors 'none'`, and `Server: nginx` with no version. The ingest host: `Access-Control-Allow-Origin: *` and no CSP. (auto: `h=$(curl -sk -D - -o /dev/null https://localhost/api/v1/version) && grep -qi '^strict-transport-security: max-age=31536000; includeSubDomains' <<<"$h" && grep -qi '^x-content-type-options: nosniff' <<<"$h" && grep -qi "^content-security-policy:.*frame-ancestors 'none'" <<<"$h" && grep -qi '^server: nginx.$' <<<"$h" && i=$(curl -sk -D - -o /dev/null https://in.localhost/health) && grep -qi '^access-control-allow-origin: \*' <<<"$i" && grep -qi '^strict-transport-security' <<<"$i" && ! grep -qi '^content-security-policy' <<<"$i"`)
8. nginx alone sets the client IP header for the apps. (auto: `DC exec nginx nginx -T 2>/dev/null | grep -qF 'proxy_set_header X-Real-IP $remote_addr;' && DC exec nginx nginx -T 2>/dev/null | grep -qF 'proxy_set_header X-Forwarded-For "";'`)
9. The logs hold no IP and no user agent, and ingest requests aren't logged at all. (auto: `curl -sk -A MieruProbe/1.0 -o /dev/null https://localhost/api/v1/version && curl -sk -A MieruProbe/1.0 -o /dev/null https://in.localhost/version && l=$(DC logs --no-log-prefix nginx) && grep -q 'GET /api/v1/version' <<<"$l" && ! grep -qE 'MieruProbe|([0-9]{1,3}\.){3}[0-9]{1,3}|GET /version' <<<"$l"`)
10. With `ingest-1` stopped, ingest still answers every request. (auto: `DC stop ingest-1 && c=$(for i in $(seq 20); do curl -sk -o /dev/null -w '%{http_code}\n' https://in.localhost/health; done); DC start ingest-1; test "$(grep -c '^200$' <<<"$c")" -eq 20`)
11. A rolling restart loses nothing, and nginx follows the new container addresses without a reload. (human: run 300 requests to `https://in.localhost/version` in a loop. Meanwhile, recreate `ingest-1` with `--force-recreate --no-deps`, wait until it's healthy, then do the same for `ingest-2`. Expect 300 `200`s.)
12. The rehearsal leaves dev alone, and certbot is ready for T03. (human: `docker compose ps` still lists the dev databases as before. `DC --profile certbot run --rm certbot --version` prints `certbot 5.9.0`.)

## Tests to write
No pytest tests: this task changes no Python. The checks are the criteria commands above. The tutorial runs them step by step as the files grow.

## Proof of done
The two setup commands, then criteria 1-10 in order. Then `DC down`, which keeps the volumes. Expected: every command exits 0.

## Test it yourself
1. `curl -sk https://localhost/api/v1/version` shows your HEAD sha. `curl -skI https://in.localhost/health` shows the headers.
2. `docker stats --no-stream` shows each container well under its limit.
3. `curl -sv https://unknown.localhost/ -k` fails the TLS handshake (the default server).
4. Run criterion 11 yourself and watch the 300 status codes.
5. Clean up: `DC down`, then remove the `mieru-prod_*` volumes by name.

## Where to debug
Filled in after the build. Candidates: the `ingest` upstream with its `resolve` and retry settings, the ACME `location` on port 80, `add_header` inheritance in the snippets, the healthcheck commands, and the app `environment:` list.

## Notes
Decisions to record in `docs/architecture.md`:
- **Upstreams re-resolve names (`resolve`, Docker DNS at `127.0.0.11`).** A recreated container can get a new IP. Without `resolve`, nginx keeps the old one until a reload. `resolve` is in open-source nginx since 1.27.3. The alternative is `nginx -s reload` after every restart.
- **`error_log crit`.** nginx error lines always carry `client: <ip>`. The cost: failed upstream connections don't show in nginx's log. The apps' logs and the healthchecks cover them.
- **`/health` and `/health/ready` on the dashboard host go to `api`.** M0 checks `https://<dashboard host>/health/ready`. The alternative, moving the routes under `/api/v1/`, would change Python and the deploy's health checks.
- **No `env_file`.** Every app variable is listed in `compose.prod.yaml`, so nothing on the server can override the image's `GIT_SHA` by accident.
- **The domain is a variable.** T02 rehearses on `localhost`, and the real domain is chosen in T03.

## Review
