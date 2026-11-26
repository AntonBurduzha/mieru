# Mieru

Privacy-first web analytics for small sites: a 1 KB script, no cookies, one page of numbers.
"See your traffic, not your visitors."

Mieru is a Plausible-style analytics SaaS built end to end: a write-heavy ingest pipeline (FastAPI and Redis Streams), a columnar event store (ClickHouse), a React dashboard, and the operations to run it all on one server.

**Status:** early development, working towards the MVP ([roadmap](docs/roadmap.md)). The write path runs end to end: the 1 KB tracking script, ingest, the Redis stream, the writer and ClickHouse, with a pipeline test in CI. The dashboard and accounts come next.

## Run it locally

You need Docker, [uv](https://docs.astral.sh/uv/) and Git.

```bash
cp .env.example .env
docker compose up -d                    # Postgres, Redis, ClickHouse
cd apps/backend
uv sync
uv run alembic upgrade head             # the Postgres tables
uv run python -m mieru.tools.ch_migrate # the ClickHouse tables
uv run pytest                           # unit and integration tests
uv run uvicorn --factory mieru.api.app:create_app --reload --port 8000
```

In another terminal, `curl localhost:8000/health/ready` prints `{"postgres":"ok","redis":"ok","clickhouse":"ok"}`.

There are no accounts yet, so a site is created from the command line, in `apps/backend`:

```bash
uv run python -m mieru.tools.create_site --org-name Me --domain example.com --timezone Europe/Kyiv
# site_id=1 organization_id=<uuid> domain=example.com
```

## Production stack, rehearsed locally

`compose.prod.yaml` is the stack the server runs: Caddy with TLS in front of two ingest replicas, the api, the writer, the worker, Postgres, Redis and ClickHouse. On a Mac it runs on `localhost`, where Caddy signs its own certificates (ports 80 and 443 must be free):

```bash
docker build -f apps/backend/Dockerfile --build-arg GIT_SHA=$(git rev-parse HEAD) \
  -t ghcr.io/antonburduzha/mieru-app:local .   # builds the tracker too
prod() { docker compose -f compose.prod.yaml --env-file ops/rehearsal.env "$@"; }
prod up -d --wait postgres redis clickhouse
prod run --rm --no-deps api alembic upgrade head          # the worker needs the salts table
prod run --rm --no-deps api python -m mieru.tools.ch_migrate
# A site to send events for, made before ingest starts, so its startup loads it.
# A second rehearsal prints conflict instead: the site is still in the volume
prod run --rm --no-deps api python -m mieru.tools.create_site --org-name Me --domain example.com
prod up -d --wait
prod logs writer worker | grep startup_complete    # both started
curl -sk https://localhost/api/v1/version       # the commit the image was built from
curl -sk https://in.localhost/health/ready      # {"redis":"ok"}
curl -sk -D - -o /dev/null -H 'Accept-Encoding: gzip' https://in.localhost/js/p.js   # 200, content-encoding: gzip
# The body the tracker sends: 202, with no x-mieru-dropped header
curl -sk -i -X POST https://in.localhost/api/event -H 'Content-Type: text/plain' \
  -d '{"n":"pageview","d":"example.com","u":"https://example.com/","r":null,"p":{}}'
# Within a few seconds: 1 on a first rehearsal, one more on each later one
prod exec clickhouse sh -c 'clickhouse-client -u mieru --password "$CLICKHOUSE_PASSWORD" -q "SELECT count() FROM mieru.events"'
prod down                                       # keeps the volumes; prod down -v deletes them
```

## Layout

| Path | What |
|---|---|
| `apps/backend` | Python 3.13 package `mieru` (FastAPI): ingest, api, writer and worker |
| `apps/web` | The React dashboard (not started) |
| `apps/tracker` | The tracking script: plain JavaScript, at most 1 KB gzipped, tested in Chromium with Playwright |
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
