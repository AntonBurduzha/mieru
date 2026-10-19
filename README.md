# Mieru

Privacy-first web analytics for small sites: a 1 KB script, no cookies, one page of numbers.
"See your traffic, not your visitors."

Mieru is a Plausible-style analytics SaaS, built end to end: a write-heavy ingest pipeline,
a columnar event store (ClickHouse), a dashboard (Nuxt), billing, alerts and operations on a single VPS.

**Status:** early development. Repository skeleton and tooling only; nothing runs yet.

## Layout

| Path | What |
|---|---|
| `apps/backend` | Python 3.13 package `mieru` (FastAPI): ingest, api, writer, worker |
| `apps/web` | Nuxt 4 dashboard |
| `apps/tracker` | The tracking script |
| `ops/` | Server bootstrap, deploy, backups, telemetry and load tests |
| `docs/` | Architecture, ADRs, specs, runbooks, feature checklist |

## Docs

- [Architecture](docs/architecture.md)
- [Architecture decision records](docs/adr/)
- [Future features checklist](docs/features.json)
