# Mieru

Privacy-first web analytics for small sites: a 1 KB script, no cookies, one page of numbers.
"See your traffic, not your visitors."

Mieru is a Plausible-style analytics SaaS built end to end: a write-heavy ingest pipeline (FastAPI and Redis Streams), a columnar event store (ClickHouse), a React dashboard, and the operations to run it all on one server.

**Status:** early development, working towards the MVP ([roadmap](docs/roadmap.md)). The backend skeleton runs: the dashboard API and the ingest app answer health checks, and CI tests them against real databases. Nothing collects events yet.

## Run it locally

You need Docker, [uv](https://docs.astral.sh/uv/) and Git.

```bash
cp .env.example .env
docker compose up -d                    # Postgres, Redis, ClickHouse
cd apps/backend
uv sync
uv run pytest                           # unit and integration tests
uv run uvicorn --factory mieru.api.app:create_app --reload --port 8000
```

In another terminal, `curl localhost:8000/health/ready` prints `{"postgres":"ok","redis":"ok","clickhouse":"ok"}`.

## Production stack, rehearsed locally

`compose.prod.yaml` is the stack the server runs: Caddy with TLS in front of two ingest replicas, the api, Postgres, Redis and ClickHouse. On a Mac it runs on `localhost`, where Caddy signs its own certificates (ports 80 and 443 must be free):

```bash
docker build -f apps/backend/Dockerfile --build-arg GIT_SHA=$(git rev-parse HEAD) \
  -t ghcr.io/antonburduzha/mieru-app:local .
docker compose -f compose.prod.yaml --env-file ops/rehearsal.env up -d --wait
curl -sk https://localhost/api/v1/version       # the commit the image was built from
curl -sk https://in.localhost/health/ready      # {"redis":"ok"}
docker compose -f compose.prod.yaml --env-file ops/rehearsal.env down
```

## Layout

| Path | What |
|---|---|
| `apps/backend` | Python 3.13 package `mieru` (FastAPI): ingest, api, writer and worker |
| `apps/web` | The React dashboard (not started) |
| `apps/tracker` | The tracking script (not started) |
| `ops/` | Server configuration and scripts |
| `docs/` | Product, architecture, conventions, roadmap and task specs |

## Docs

- [Product](docs/product.md): what Mieru does, for whom, and what is in the MVP
- [Architecture](docs/architecture/README.md): the system design and its trade-offs
- [Frontend](docs/frontend.md): the design of the React app
- [Conventions](docs/conventions.md): layout, names, code style, testing, security
- [Roadmap](docs/roadmap.md): what's being built now and next
- [Feature checklist](docs/features.json)

## How work is done

Each task gets a public spec in [`docs/tasks/`](docs/tasks/) with acceptance criteria and a proof of done. It is then built either by hand or by Claude Code agents: one writes the tests, the main session implements, a separate agent verifies the proof, and another reviews the diff. The developer approves the spec, tests the result and merges. The full workflow is in [docs/workflow.md](docs/workflow.md).
