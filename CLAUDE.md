# Mieru: context for Claude Code

Privacy-first web analytics (Plausible-like): a 1 KB tracking script, no cookies, one dashboard page.
Monorepo: `apps/backend` (Python 3.13, uv, FastAPI, package `mieru` under `src/`), `apps/web` (React, not started), `apps/tracker` (plain JS, not started).

## Docs: read the one the work needs
- `docs/product.md`: what Mieru does and for whom; stories with acceptance criteria; MVP scope.
- `docs/architecture.md`: the system design. Section 13 holds the decisions.
- `docs/frontend.md`: the design of the React app.
- `docs/conventions.md`: layout, names, code style, tests, security. It wins when docs disagree.
- `docs/roadmap.md`: the "Now" block and the task list.
- `docs/workflow.md`: how a task runs (`/task`, `/verify`, `/status`).
- `docs/features.json`: one entry per feature, with the command that proves it.

## State today
Built: the `api` and `ingest` apps with `/health`, `/health/ready` and version endpoints; settings; JSON logs with request ids; the error handler; local Compose; CI against real databases; the image pushed to GHCR from `main`.
Not built yet: Postgres models, auth, the ingest pipeline, writer, worker, web app, tracker, the server.

## Rules that never bend
- Layers: `api/routes -> services -> repositories -> models`. Routes never touch SQL.
- Pipelines (ingest, writer): pure step functions plus thin I/O adapters.
- Every tenant query is scoped by `org_id` or `site_id`. The ClickHouse `site_id` comes from the server, never the client.
- Never log or store IPs, user agents or request bodies. The raw IP never leaves `mieru.ingest`.
- Time comes from `mieru.core.clock`, never `datetime.now()`.
- Errors: raise `AppError` subclasses. Async everywhere; blocking work goes through `asyncio.to_thread`.
- Never delete, skip or weaken a test. If a test looks wrong, say so and stop.
- Never mark a feature in `docs/features.json` as passing without running its check.
- Public files never link to private ones (`docs/learn/`, `CLAUDE.local.md`).

## Working on a task
Each task has a spec in `docs/tasks/T<NN>-<slug>.md`. Edit only the files the spec allows, write the tests first, run the proof-of-done commands and show their output. Never push; the developer pushes and opens the PR. Details: `docs/workflow.md`.
The developer is the only author: commits and PR descriptions carry no `Co-Authored-By` trailer and no "Generated with Claude Code" line.

## Commands
From `apps/backend`:
- `uv run pytest -m "not integration and not e2e"`: unit tests, no services needed
- `uv run pytest`: unit and integration tests; needs `docker compose up -d`
- `uv run ruff check --fix . && uv run ruff format . && uv run mypy src tests`
- `uv run uvicorn --factory mieru.api.app:create_app --reload --port 8000`
- `uv run uvicorn --factory mieru.ingest.app:create_app --reload --port 8001`

From the repo root:
- `docker compose up -d`: Postgres, Redis, ClickHouse

## When compacting
Keep: the task id and its status, the files changed, failing tests, open questions.
