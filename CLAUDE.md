# Mieru - context for Claude Code
Privacy-first web analytics (Plausible-like). Monorepo:
apps/backend (Python 3.13, uv, FastAPI, src/mieru), apps/web (Nuxt 4, TS, pnpm), apps/tracker (plain JS).
Design: docs/architecture.md. Decisions: docs/adr/. Feature checklist: docs/features.json.

## Implementing a spec
When a prompt names a spec in docs/specs/: edit only the files the spec allows, write or keep the
tests, run the checks, show the output. Never delete, skip or weaken a test.

## Conventions
- Layers: api/routes -> services -> repositories -> models. Routes never touch SQL.
- Pipelines (ingest, writer): pure step functions + thin I/O adapters.
- Every tenant query is scoped by org_id/site_id. site_id for ClickHouse comes from the server.
- Never log IPs, user agents or request bodies. The raw IP never leaves mieru.ingest.
- Time comes from mieru.core.clock, never datetime.now().
- Errors: raise AppError subclasses. Async everywhere; blocking work via asyncio.to_thread.

## Commands (from apps/backend unless noted)
- uv run pytest            # unit + integration (needs `docker compose up -d` at repo root)
- uv run pytest -m e2e     # full pipeline
- uv run ruff check --fix . && uv run ruff format . && uv run mypy src
- pnpm --dir apps/web test / lint / typecheck ; pnpm --dir apps/tracker build
- docker compose up -d     # postgres, redis, clickhouse

## Feature checklist
Never mark a feature in docs/features.json as passing without running its check.
When compacting, keep: files changed, failing tests, open questions.
