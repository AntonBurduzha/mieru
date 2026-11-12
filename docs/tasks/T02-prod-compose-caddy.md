---
id: T02
title: Production compose, Caddy and TLS, rehearsed locally
mode: human
status: done
features: []
depends: [T01]
branch: t02-prod-compose-caddy
---

# T02 Production compose, Caddy and TLS, rehearsed locally

## Goal
The production stack runs on the Mac from one file, `compose.prod.yaml`. Caddy terminates TLS and routes by host and path. Ingest stays reachable while one of its two replicas is down or being recreated. Only Caddy publishes ports, every container has a memory limit, and no client IP address or user agent reaches a log. T03 runs the same files on the server, where Caddy gets real certificates by itself.

## Context to read
- `docs/architecture.md`: section 11 ("Containers", "Routing", "How Caddy is set up", "Network and access"), section 5.1 step 5 (`X-Real-IP`), and section 13 rows 19, 24, 25 and 27-29.
- `docs/conventions.md`: section 1 (layout), section 7.5 (headers, server and containers), section 7.7 row "Server and deploy".
- `docs/frontend.md` section 3: the dashboard CSP.
- `compose.yaml`: the database healthchecks to reuse. `ops/clickhouse/low-memory.xml`.
- `apps/backend/Dockerfile`: the `GIT_SHA` build arg and uid 10001. The image has no curl.
- `apps/backend/src/mieru/ingest/routes.py` and `api/routes/health.py`: `/health`, `/health/ready`, `/version`, `/api/v1/version`.
- Caddy docs: [`reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) (load balancing, health checks, headers, transport), [`log`](https://caddyserver.com/docs/caddyfile/directives/log) (the `filter` format), and [automatic HTTPS](https://caddyserver.com/docs/automatic-https).

## Scope
- **In:**
  - `compose.prod.yaml`: caddy; `ingest-1`, `ingest-2`, `api`; postgres, redis, clickhouse; the `edge` and `internal` networks; healthchecks, restart policies and memory limits.
  - `ops/caddy/Caddyfile`: routing, health checks and retries, `X-Real-IP`, the log filter, security headers, and closing unknown hosts.
  - Removing the first build's nginx files and the certbot service.
  - The rehearsal variables, and the README section.
- **Out:**
  - T03: the real domain and certificates, DNS, the provider firewall, Docker's log size cap.
  - T04: `ops/deploy.sh` and the rolling restart. T11: the `writer` and `worker` containers.
  - T20: serving the web app's files. Until then, `/` answers a placeholder.
  - T09: the `/js/p.js` headers. T26: the header and port-scan audit. No Python changes.
- **Files allowed to change:**
  - `compose.prod.yaml`;
  - `ops/caddy/Caddyfile` (new);
  - `ops/nginx/**` (delete all of it);
  - `ops/rehearsal.env`;
  - `README.md`: the section "Production stack, rehearsed locally".

  `docs/architecture.md`, `docs/conventions.md` and `docs/frontend.md` were updated together with this spec.

## Interfaces
- **Project:** `name: mieru-prod`, so it never shares containers or volumes with the dev project `mieru`.
- **Variables** (interpolated, each `${VAR:?}`): `MIERU_TAG`, `MIERU_DOMAIN` (the dashboard host; ingest is `in.${MIERU_DOMAIN}`), `POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD` and `ACME_EMAIL`. They come from `/opt/mieru/.env` on the server and from `ops/rehearsal.env` on the Mac. The passwords go into URLs, so they must be URL-safe (for example `openssl rand -hex 24`).
- **App image:** `ghcr.io/antonburduzha/mieru-app:${MIERU_TAG}`. Settings come from an explicit `environment:` list:
  - `ENV: prod`;
  - the three DSNs, built from the passwords, with service names as hosts;
  - `APP_BASE_URL: https://${MIERU_DOMAIN}`;
  - `INGEST_BASE_URL: https://in.${MIERU_DOMAIN}`.

  No `env_file` and no `GIT_SHA`: the image's value must win.
- **App commands:** `uvicorn --factory mieru.ingest.app:create_app --host 0.0.0.0 --port 8001 --no-proxy-headers` (for `api`: `mieru.api.app:create_app` on port 8000). uvicorn keeps idle connections for 5 s by default. Healthchecks: `python -c` with `urllib.request` on `/health`.
- **Images:** `caddy:2.11.9-alpine`. Postgres, Redis and ClickHouse use the same tags as `compose.yaml`.
- **Redis:** the dev flags plus `--maxmemory 400mb --maxmemory-policy noeviction`.
- **Memory limits:**

  | caddy | ingest-1, ingest-2 | api | postgres | redis | clickhouse |
  |---|---|---|---|---|---|
  | 128m | 256m each | 384m | 512m | 512m | 2560m |
- **Networks:** `edge` (caddy and the apps) and `internal`, with `internal: true` (the apps and the databases).
- **The caddy service:**
  - ports `80:80` and `443:443`, TCP only;
  - environment `MIERU_DOMAIN` and `ACME_EMAIL`;
  - volumes `./ops/caddy:/etc/caddy:ro` (the directory, not the file: see Notes), `caddy-data:/data` (certificates and the local CA; it must persist) and `caddy-config:/config`.
  - `depends_on`: `ingest-1` and `ingest-2` with `service_started`, and not `api`. Ingest must come up even when a database holds `api` back. Caddy's health checks handle readiness.
  - Healthcheck: `wget` on `http://127.0.0.1:8080/healthz`, a site that listens on loopback only and has no log.
- **Caddyfile:**
  - **Global:** `email {$ACME_EMAIL}`; `servers { protocols h1 h2 }`; and `log default` at level `INFO` with a `filter` that deletes `request>remote_ip`, `request>remote_port`, `request>client_ip` and `request>headers`. The admin API stays on its default, loopback inside the container, so `caddy reload` works.
  - **TLS:** automatic. For `localhost` and `in.localhost`, Caddy signs certificates with its own local CA. For a real domain, it uses Let's Encrypt.
  - **Every proxy:** `header_up X-Real-IP {remote_host}`, `header_up -X-Forwarded-For`, `header_up -Forwarded`, and `transport http { dial_timeout 1s; keepalive 4s }` (below uvicorn's 5 s).
  - **The dashboard host `{$MIERU_DOMAIN}`:**
    - `/api/*` → `api:8000`;
    - exactly `/health` and `/health/ready` → `api:8000`;
    - everything else → `200` with the text `Mieru is coming soon`;
    - an access log with a filter that also deletes `request>tls`, `resp_headers` and the query string;
    - the CSP from `docs/frontend.md`.
  - **The ingest host `in.{$MIERU_DOMAIN}`:**
    - every path → `reverse_proxy ingest-1:8001 ingest-2:8001` with `lb_policy round_robin`, `lb_try_duration 5s`, `lb_try_interval 100ms`, `fail_duration 5s`, `health_uri /health`, `health_interval 2s` and `health_timeout 1s`;
    - no access log;
    - `Access-Control-Allow-Origin: *`.
  - **Both hosts:** the headers in conventions section 7.5, plus `-Server` and `-Via`.
  - **Unknown hosts:** a request whose `Host` matches neither site gets its connection closed (`abort`) on both ports. An unknown TLS server name fails the handshake.
- **Removed:**
  - `ops/nginx/` and its self-signed certificate script;
  - the `certbot` service;
  - the `letsencrypt` and `certbot-www` volumes.

## Acceptance criteria
Every check runs from the repo root, after this setup:

```bash
docker build -f apps/backend/Dockerfile --build-arg GIT_SHA=$(git rev-parse HEAD) -t ghcr.io/antonburduzha/mieru-app:local .
```

`DC` stands for `docker compose -f compose.prod.yaml --env-file ops/rehearsal.env`. Write it out in full, and run the commands under bash.

1. The file is valid, and a missing tag stops it. (auto: `DC config --quiet && ! MIERU_TAG= DC config --quiet`)
2. Every service becomes healthy, and the Caddyfile is valid. (auto: `DC up -d --wait && DC exec caddy caddy validate --config /etc/caddy/Caddyfile`)
3. Only caddy publishes ports, and the databases' network has no route out. (auto: `test "$(DC ps --format '{{.Service}} {{.Ports}}' | grep -- '->' | cut -d' ' -f1)" = caddy && test "$(docker network inspect mieru-prod_internal -f '{{.Internal}}')" = true`)
4. Every running container has a memory limit. (auto: `docker inspect -f '{{.Name}} {{.HostConfig.Memory}}' $(DC ps -q) | awk '$2 == 0 {bad = 1} END {exit bad}'`)
5. Plain HTTP redirects to HTTPS and keeps the query, and unknown hosts get no answer. (auto: `test "$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' 'http://localhost/x?a=1')" = "308 https://localhost/x?a=1" && ! curl -s -o /dev/null -H 'Host: unknown.example' http://localhost/ && ! curl -sk -o /dev/null -H 'Host: unknown.example' https://localhost/ && ! curl -sk -o /dev/null --resolve unknown.localhost:443:127.0.0.1 https://unknown.localhost/`)
6. Routing reaches the right app, which runs the image's commit: `/api/v1/version` returns the HEAD sha, `/health/ready` is all `ok` on both hosts, and `/` shows the placeholder. (auto: `test "$(curl -sk https://localhost/api/v1/version)" = "{\"version\":\"$(git rev-parse HEAD)\"}" && curl -skf https://localhost/health/ready | grep -q '"clickhouse":"ok"' && curl -skf https://in.localhost/health/ready | grep -q '"redis":"ok"' && curl -skf https://localhost/ | grep -q 'Mieru is coming soon'`)
7. The headers are set on both hosts, proxied responses included. The dashboard host has HSTS with `includeSubDomains`, `nosniff` and a CSP with `frame-ancestors 'none'`. Neither host sends `Server` or `Via`. The ingest host has `Access-Control-Allow-Origin: *` and no CSP. (auto: `h=$(curl -sk -D - -o /dev/null https://localhost/api/v1/version) && grep -qi '^strict-transport-security: max-age=31536000; includeSubDomains' <<<"$h" && grep -qi '^x-content-type-options: nosniff' <<<"$h" && grep -qi "^content-security-policy:.*frame-ancestors 'none'" <<<"$h" && ! grep -qiE '^(server|via):' <<<"$h" && i=$(curl -sk -D - -o /dev/null https://in.localhost/health) && grep -qi '^access-control-allow-origin: \*' <<<"$i" && grep -qi '^strict-transport-security' <<<"$i" && ! grep -qiE '^(content-security-policy|server|via):' <<<"$i"`)
8. Caddy alone sets the client IP header for the apps. (auto: `c=$(DC exec caddy caddy adapt --config /etc/caddy/Caddyfile) && grep -qiF '"x-real-ip":["{http.request.remote.host}"]' <<<"$c" && grep -qiE '"delete":\[[^]]*"x-forwarded-for"' <<<"$c"`)
9. No log line holds a client IP, a user agent or a query string, even for broken TLS handshakes and unknown hosts, and ingest requests aren't logged. (auto: `t=$(date -u +%Y-%m-%dT%H:%M:%SZ) && curl -sk -A MieruProbe/1.0 -o /dev/null 'https://localhost/api/v1/version?probe=1' && curl -sk -A MieruProbe/1.0 -o /dev/null https://in.localhost/version && curl -s -A MieruProbe/1.0 -o /dev/null http://localhost:443/; curl -sk -A MieruProbe/1.0 -o /dev/null -H 'Host: unknown.example' https://localhost/; echo | openssl s_client -connect localhost:443 -servername localhost -tls1 >/dev/null 2>&1; sleep 1; l=$(DC logs --no-log-prefix --since "$t" caddy) && grep -q '"uri":"/api/v1/version"' <<<"$l" && ! grep -qE 'MieruProbe|probe=1|"host":"in\.localhost"|([0-9]{1,3}\.){3}[0-9]{1,3}' <<<"$l"`)
10. With `ingest-1` stopped, ingest still answers every request. (auto: `DC stop ingest-1 && c=$(for i in $(seq 20); do curl -sk -o /dev/null -w '%{http_code}\n' https://in.localhost/health; done); DC start ingest-1; test "$(grep -c '^200$' <<<"$c")" -eq 20`)
11. A rolling restart loses nothing, and Caddy finds the recreated containers without a reload. (human: run 300 requests to `https://in.localhost/version` in a loop. Meanwhile, recreate `ingest-1` with `--force-recreate --no-deps` and wait until it's healthy, then do the same for `ingest-2`. Expect 300 `200`s.)
12. The rehearsal leaves dev alone, and Caddy holds the local certificates. (human: `docker compose ps` still lists the dev databases as before. `DC exec caddy caddy version` prints `v2.11.9`. `DC exec caddy ls /data/caddy/certificates/local` lists `localhost` and `in.localhost`.)

## Tests to write
No pytest tests: this task changes no Python. The checks are the criteria commands above, and the tutorial runs them step by step as the files grow.

## Proof of done
The setup command, then criteria 1-10 in order, then `DC down`, which keeps the volumes. Run criterion 10 five times in a row, since its old failure was intermittent. Expected: every command exits 0.

## Test it yourself
1. `curl -sk https://localhost/api/v1/version` shows your HEAD sha. `curl -skI https://in.localhost/health` shows the headers.
2. `docker stats --no-stream` shows each container well under its limit.
3. Run criterion 11 yourself and watch the 300 status codes. Then run `DC exec caddy caddy reload --config /etc/caddy/Caddyfile` during the loop: still all `200`.
4. `DC logs caddy`: look for anything that identifies a visitor.
5. Clean up: `DC down`, then remove the `mieru-prod_*` volumes by name, including the first build's `mieru-prod_letsencrypt` and `mieru-prod_certbot-www`.

## Where to debug
No Python, so no breakpoints: each place is watched through Caddy's logs, its adapted JSON or `curl`. Line numbers are as of the working tree on 2026-11-12. `DC` is the compose command from the criteria.

1. **Which upstream gets an ingest request, and whether a failed dial is retried**: `ops/caddy/Caddyfile:105` (`lb_try_duration` in the ingest `reverse_proxy`)
   - Why here: this line turns a dial error on a stopped replica into a retry on the other one. Without it, criterion 10 fails every run with a `502`.
   - Trigger: criterion 10, or `DC stop ingest-1` and then `curl -sk -o /dev/null -w '%{http_code} %{time_total}\n' https://in.localhost/health`.
   - Look at: `DC logs -f caddy | grep -E 'health_checker|no upstreams'`. Expect `lookup ingest-1 ... no such host`, then `host is up` after `DC start ingest-1`. Every status is `200`, and none takes longer than about 0.2 s.
2. **What a log line may contain**: `ops/caddy/Caddyfile:15` (global `log default` filter) and `:73` (the dashboard access log filter)
   - Why here: these two filters are the only thing between a request and a client IP, user agent or query string in a log. A field that isn't listed goes to the log as it is.
   - Trigger: criterion 9, or `curl -sk -A Probe 'https://localhost/api/v1/version?x=1'`.
   - Look at: `DC logs --no-log-prefix caddy | tail -5`. The access line has only `proto`, `method`, `host`, `uri` with no query, `size` and `status`.
3. **Which client IP the apps see**: `ops/caddy/Caddyfile:53` (`header_up X-Real-IP {remote_host}` in `(proxy_settings)`)
   - Why here: `ingest` builds the visitor id from this header. If a client-sent `X-Forwarded-For` or `X-Real-IP` gets through, visitors can be faked.
   - Trigger: `DC exec caddy caddy adapt --config /etc/caddy/Caddyfile --pretty | grep -n -i -A3 'x-real-ip'`.
   - Look at: `set` holds `X-Real-Ip: {http.request.remote.host}`, and `delete` holds `X-Forwarded-For` and `Forwarded`, in both `reverse_proxy` handlers.
4. **Which site answers a request**: `ops/caddy/Caddyfile:84` (`@api` matcher) and `:118`/`:122` (the `abort` catch-alls)
   - Why here: these lines decide between `api`, the placeholder and a closed connection. A wrong matcher sends `/health` to the placeholder. A missing catch-all answers unknown hosts with an empty `200`.
   - Trigger: criteria 5 and 6, or `curl -sk -o /dev/null -w '%{http_code}\n' -H 'Host: unknown.example' https://localhost/`.
   - Look at: `/api/v1/version` returns JSON, `/` returns `Mieru is coming soon`, and an unknown `Host` makes curl fail.
5. **Which Caddyfile a reload reads**: `compose.prod.yaml:33` (the `./ops/caddy:/etc/caddy:ro` mount)
   - Why here: a single-file mount keeps the old inode after `git pull` or an editor save, so the reload reads stale config or none at all.
   - Trigger: edit `ops/caddy/Caddyfile`, then `DC exec caddy caddy reload --config /etc/caddy/Caddyfile`.
   - Look at: `DC exec caddy tail -3 /etc/caddy/Caddyfile` matches the file on the Mac, and the reload prints no error.

## Notes
- **Why Caddy and not nginx.** The first build used nginx (commits `fe0d18f` to `1515078`). It failed criterion 10 intermittently:
  1. A stopped replica left Docker's DNS during a request.
  2. nginx's upstream `resolve` removed the peer and marked the upstream `single`.
  3. That cancelled the request's retry and returned a `504`.

  Fixed IPs worked around it. Instead, a spike ran Caddy against every criterion here: 20/20 runs of criterion 10, 300/300 twice for criterion 11, a third replica added with no reload, and no client IP in any log. The decisions are recorded in architecture section 13, rows 19, 24, 25 and 27-29.
- **The spike's surprises, now handled above:**
  - A known TLS name with an unknown `Host` got an empty `200` (now `abort`).
  - Caddy adds `Via` (now removed).
  - Caddy advertises HTTP/3 (now off).
  - The redirect is `308`, not `301`.
- **The Caddyfile is mounted as a directory.** A bind mount of a single file follows the file's inode. `git pull`, `sed -i` and most editors replace the file with a new one, so the container keeps the old file (Linux) or loses it (Docker Desktop), and `caddy reload` reads stale config or fails with `no such file or directory`. Mounting `ops/caddy/` follows the directory, so a reload always sees the current file. Found while preparing the build on 2026-11-12.
- **Scaling past two replicas** uses `dynamic a` on a shared network alias. It's not needed in T02.

## Review
**Verifier (2026-11-12): NEED_HUMAN.** All `auto:` criteria pass. Criteria 11 and 12 wait for the developer's test.

| # | Result | Evidence |
|---|---|---|
| setup | pass | image built, `naming to ghcr.io/antonburduzha/mieru-app:local done` |
| 1 | pass | exit 0; with the tag empty: `required variable MIERU_TAG is missing a value` |
| 2 | pass | 7 containers `Healthy`; `Valid configuration` |
| 3 | pass | only caddy publishes `80->80/tcp, 443->443/tcp`; `mieru-prod_internal` Internal=true |
| 4 | pass | every limit set and equal to the spec's table |
| 5 | pass | `308 https://localhost/x?a=1`; unknown host: curl 52 (HTTP), 92 (HTTPS), 35 (unknown SNI) |
| 6 | pass | version = HEAD `a4ec871`; ready `ok` on both hosts; placeholder on `/` |
| 7 | pass | HSTS, nosniff and the CSP on the dashboard; ACAO `*`, HSTS and no CSP on ingest; no `Server` or `Via` |
| 8 | pass | adapted JSON sets `x-real-ip` from `{http.request.remote.host}` and deletes `x-forwarded-for` |
| 9 | pass | access lines hold only proto, method, host, uri, size and status |
| 10 | pass | 5 runs, 20/20 `200`s each |
| 11 | human | not confirmed yet |
| 12 | human | read-only parts pass (`v2.11.9`; certificates for `localhost` and `in.localhost`); not confirmed yet |

Scope: clean. Nothing under `apps/` changed.

**Reviewer (2026-11-12): no must-fix.** Findings, all left optional for gate 2:
1. Should-fix: the global log filter keeps the query string in Caddy's error lines (a `502` on `/api/...?...` would log it). Reproduced: a `502` logged `"uri":"/api/v1/version?secret=abc"` and the `tls` object. **Fixed** at gate 2: the global filter also deletes `request>tls` and strips the query.
2. Should-fix: ACME challenge lines (`remote`) and admin API lines (`remote_ip`, `remote_port`, `headers`) log addresses at INFO. These are Let's Encrypt validators and the local `caddy reload`, not visitors. Reproduced for the admin API (`remote_ip`, `User-Agent`). **Fixed** at gate 2: the global filter deletes top-level `remote`, `remote_ip`, `remote_port` and `headers`. The ACME line can only be seen against a real CA: check it at T03's first issuance.
3. Should-fix (T03), left for T03: IPv6 visitors reach Caddy through docker-proxy, so their address becomes the bridge gateway. Fix in T03: IPv6 on `edge`, or no AAAA record.
4. Should-fix: "Where to debug" was empty. Done above.
5. Nit, left optional: caddy runs as root. Option: `cap_drop: [ALL]`, `cap_add: [NET_BIND_SERVICE]`, `no-new-privileges`, or defer to T26.
6. Nit, left optional: Caddy's own error responses and the `:80` redirect skip the header block (`Server: Caddy`, no HSTS). Option: `handle_errors` per site and a deferred `header >Access-Control-Allow-Origin`; or T26.
7. Nit, left optional: a `POST` on a pooled connection that uvicorn closes during shutdown gets a `502` with no retry. Recheck criterion 11 with `POST` once the event endpoint exists (T04 or later).
8. Nit, left optional: `email` also adds ZeroSSL as a fallback issuer. Option: `cert_issuer acme` to keep Let's Encrypt only.

**Final verifier (2026-11-12, after gate 2): DONE.** Setup, criteria 1-10 and four more runs of criterion 10 all exit 0. Criterion 9 also confirmed the gate-2 filter fields in the running config. The developer confirmed criteria 11 and 12 on 2026-11-12. Scope is clean, and nothing under `apps/` changed. Note for T03: criterion 9's IPv4 regex also matches internal container addresses that the health checker logs while a replica is down, so run it with both replicas up.
