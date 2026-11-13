# Mieru: roadmap

> What's being built now, what comes next, and every task up to the MVP. The scope is in [product.md](product.md) section 5. How a task runs, from spec to merge, is in [workflow.md](workflow.md).

---

## Now

<!-- /task edits these fields in place when Next or an open question changes. What's in progress is the Status column below. -->

| | |
|---|---|
| **Next** | T05 (human) |
| **Open questions** | IPv6: T03 publishes no AAAA record, so every visitor connects over IPv4; IPv6 on `edge` waits for T26 |
| **Updated** | 2026-11-13 |

---

## How to read the task table

- **Mode** is how the task is built:
  - `agent` (the default): agents implement it, and you approve the spec and test the result.
  - `human`: you write the code from a private tutorial; Claude writes the docs, and agents review and verify your code.

  `/task <id> --human` or `--agent` overrides the mode before the build starts.
- **Status** goes `todo`, `spec`, `build`, `test` (agent mode only), `done`. What each status means and what you do at it: [workflow.md](workflow.md) section 3.

- **Features** are the ids in [features.json](features.json) that the task makes pass. A task's spec may make a feature's check more precise, never weaker.
- **Depends** lists the tasks that must be `done` first.
- **The order** builds the thinnest working loop first: one real page view from a real site into ClickHouse, in production. Each part is made complete after that loop works.

---

## M0: Ship the skeleton

**Done when:** `https://<api domain>/health/ready` and `https://<ingest domain>/health/ready` answer `ok` from the real server, a merge to `main` deploys by itself, and a rollback to the previous image has been rehearsed.

| ID | Task | Mode | Features | Depends | Status |
|---|---|---|---|---|---|
| T01 | **Config and startup hardening.** Settings safe by default: an unset `ENV` must not show error details or send cookies without `Secure`. The `.env` path doesn't depend on the working directory. `session_ttl_days` accepts 1. The crash-test flag is refused in prod. Database URLs are secret. No local variables in local tracebacks. Apps are built by `uvicorn --factory`, not at import time. The api starts when ClickHouse is down and reports it in `/health/ready`. Also: the `DTZ` lint rule, mypy on tests, a coverage report, ruff check before format in pre-commit, and the image built (not pushed) on every PR | agent | | | done |
| T02 | **Production compose, Caddy and TLS, rehearsed locally.** `compose.prod.yaml`, the Caddyfile (routing, active and passive health checks and retries, `X-Real-IP`, the log filter, no ingest access log, headers), memory limits. The whole stack runs on the Mac with Caddy's local certificates | human | | T01 | done |
| T03 | **Server bootstrap and first manual deploy.** Order the server, `ops/bootstrap.sh` (user, SSH, updates, Docker), provider firewall, DNS in Cloudflare (DNS only), the real domain (Caddy gets its certificates), the first deploy by hand | human | | T02 | done |
| T04 | **Automated deploy.** `ops/deploy.sh` (pull, migrate, restart ingest replicas one at a time), a CI deploy key that can only run `deploy.sh`, `deploy.yml` with the `production` environment, and a rollback drill | agent | | T03 | done |

## M1: First page view, end to end

**Done when:** a page view on one of your own sites lands in production ClickHouse within 35 seconds, and the end-to-end pipeline test passes in CI.

| ID | Task | Mode | Features | Depends | Status |
|---|---|---|---|---|---|
| T05 | **Postgres layer.** SQLAlchemy async models and sessions, Alembic, tables `organizations`, `sites`, `salts`; the test database with rollback per test; factories; a tool to create an organization and a site from the command line (until there are accounts) | human | | T01 | todo |
| T06 | **ClickHouse schema.** The `events` table and the migration runner (`mieru.tools.ch_migrate`), the test database, and site-id isolation in tests | human | | T01 | todo |
| T07 | **Ingest core.** Body validation and limits, the site cache, daily salts and the visitor hash, `202` with `X-Mieru-Dropped: unknown_site`, `XADD` to the stream, and the IP never leaving the request | human | I1 | T05 | todo |
| T08 | **Writer core.** Consumer group, batches by size or time, insert into ClickHouse, acknowledge only after the insert, clean shutdown on SIGTERM | human | | T06, T07 | todo |
| T09 | **Tracker.** Page view on load, SPA navigation, custom events with the queue, refusing `file:` pages and automated browsers, the 1 KB budget in CI, a Playwright page test | agent | T1-T4 (with T11) | | todo |
| T10 | **Worker and salt rotation.** `python -m mieru.worker`: an asyncio loop that creates tomorrow's salt at 23:00 UTC and deletes salts older than 48 hours | agent | | T05 | todo |
| T11 | **Pipeline test and first real traffic.** `tests/e2e/test_pipeline.py` in CI, the seed tool, writer and worker in the production stack, the script installed on your own sites | agent | T1-T4 | T04, T08, T09, T10 | todo |

## M2: Complete the write path

**Done when:** I2, I3, I4 and OPS1 pass. Drops are counted by reason, events carry country, device and source, sessions survive midnight, and a deploy under traffic loses nothing.

| ID | Task | Mode | Features | Depends | Status |
|---|---|---|---|---|---|
| T12 | **Ingest defences.** Bot user agents, spam referrers, the per-site rate limit (pipelined `INCR` + `EXPIRE`, then `XADD`), backpressure above the stream's high-water mark, the in-memory fallback queue when Redis is down, drop counters | human | I2 | T11 | todo |
| T13 | **Enrichment.** Country from the DB-IP file (in ingest), device, browser and OS from the user agent, source and UTM tags from the referrer and URL (in the writer) | agent | I3 | T11 | todo |
| T14 | **Sessions.** Session state in Redis, the midnight hand-over (yesterday's visitor id), the `sessions` table and materialized view, the insert dedup token, and the writer crash test | human | I4 | T13 | todo |
| T15 | **No lost events.** A drill script that sends traffic during a deploy and compares sent and stored counts; run it on the server | agent | OPS1 | T12, T14 | todo |

## M3: Accounts and the dashboard

**Done when:** A1, A2, D1-D4 and D6 pass. A new user signs up, adds a site, installs the script and sees their traffic on the dashboard.

| ID | Task | Mode | Features | Depends | Status |
|---|---|---|---|---|---|
| T16 | **Auth.** Users, memberships, server-side sessions, argon2, email verification with the dev outbox, the login limiter, the Origin check, and the tenant-isolation test | human | A1 (with T20) | T05 | todo |
| T17 | **Sites API.** Create, list, update and delete sites; domain rules; the snippet; the "is it installed?" endpoint | agent | A2 (with T23) | T16 | todo |
| T18 | **Stats query builder.** Aggregate and timeseries queries, periods in the site's timezone, filters, parameters and whitelists, the read-only ClickHouse user, query limits and `stats_unavailable` | human | D1, D2 (with T21) | T14 | todo |
| T19 | **Breakdowns and realtime.** Breakdown by every MVP property with paging, current visitors, the stats cache | agent | D3, D4 (with T22) | T18 | todo |
| T20 | **Web foundation.** The Vite app per [frontend.md](frontend.md): router, typed client, query setup, app shell, theme, auth pages, sites list, the web CI job and size budgets | agent | A1 | T16, T17 | todo |
| T21 | **Dashboard.** Date range, stat cards, the chart, filters in the URL | agent | D1, D2, D6 | T19, T20 | todo |
| T22 | **Breakdown panels.** Panels with tabs, click to filter, "show more", the realtime badge, loading, empty and error states | agent | D3, D4, D6 | T21 | todo |
| T23 | **Onboarding.** Add a site, the snippet with a copy button, the install check turning green | agent | A2 | T17, T20 | todo |

## M4: MVP launch

**Done when:** OPS2 and SEC1 pass, and the MVP success criteria in [product.md](product.md) section 6 hold.

| ID | Task | Mode | Features | Depends | Status |
|---|---|---|---|---|---|
| T24 | **Backups.** `pg_dump` nightly and ClickHouse `BACKUP` weekly to R2, and a restore drill onto a fresh server | agent | OPS2 | T15 | todo |
| T25 | **Basic monitoring.** An outside uptime check, a disk alert at 70%, the stream's age in `/health/ready`, log size caps, measured memory limits | agent | | T15 | todo |
| T26 | **Security and privacy pass.** Headers and CSP checked, `pip-audit`, `pnpm audit`, Trivy and `gitleaks` in CI, the privacy test, a port scan from outside | agent | SEC1 | T23 | todo |
| T27 | **Launch.** Mieru on your own sites and 3-5 friendly outside sites, a demo account with seeded data, status and numbers in the README | human | | T24, T25, T26 | todo |

**Why these are human tasks:**
- T02 and T03: the infrastructure you'll operate yourself.
- T05 and T06: the data layer.
- T07, T08, T12 and T14: the pipeline core.
- T16: auth.
- T18: the query builder.
- T27: the launch itself.

These are the parts you must be able to explain and defend. Agent tasks are React (already your strength), CRUD, glue and ops automation.

---

## After the MVP

Planned after the launch, roughly in this order. Each gets task ids when it's next:

1. Goals and custom-property breakdowns (D5)
2. Shared links (D7)
3. Comparison and CSV export (D8)
4. Plans, quotas and usage (A3)
5. Stripe in test mode (A4)
6. Email alerts and the weekly report (A5, A6)
7. OpenTelemetry and Grafana; the dashboard's own web vitals
8. Load tests and the rollup decision
9. The Stats API (D8)
10. A landing page

---

## Done before the replan

- **Repository skeleton:** layout, `.gitignore`, `.editorconfig`, the Claude Code settings, hooks and agents, the PR template.
- **FastAPI skeleton:**
  - the `api` and `ingest` apps with `/health`, `/health/ready` and version endpoints;
  - settings, JSON logs with request ids, and the error handler;
  - local Compose with Postgres 17, Redis 7 (AOF) and ClickHouse;
  - pre-commit.
- **CI and image:** lint, types and tests against real databases on every PR; the image pushed to GHCR from `main`.
