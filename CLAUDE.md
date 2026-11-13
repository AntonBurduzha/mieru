# Mieru: context for Claude Code

Privacy-first web analytics (Plausible-like): a 1 KB tracking script, no cookies, one dashboard page.
Monorepo: `apps/backend` (Python 3.13, uv, FastAPI, package `mieru` under `src/`), `apps/web` (React, not started), `apps/tracker` (plain JS, not started).

## Docs: read the one the work needs
- `docs/product.md`: what Mieru does and for whom; stories with acceptance criteria; MVP scope.
- `docs/architecture/`: the system design, one file per topic. `README.md` maps them; `decisions.md` holds the decisions. Read only the files the spec names.
- `docs/frontend.md`: the design of the React app.
- `docs/conventions.md`: layout, names, code style, tests, security. It wins when docs disagree.
- `docs/roadmap.md`: the "Now" block and the task list. What's built: the `done` tasks and "Done before the replan".
- `docs/workflow.md`: the developer's guide to the workflow. Agents follow the skills in `.claude/skills/` instead.
- `docs/features.json`: one entry per feature, with the command that proves it.

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
A task runs through `/task` in two runs. Run 1 writes the spec in `docs/tasks/T<NN>-<slug>.md`, gets it approved, then builds it (agent mode) or writes the private tutorial (human mode). Run 2, after the developer's test, checks, fixes and makes the task's one commit.
- Edit only the files the spec allows. Tests come first.
- The spec is committed when it's approved. Nothing else is committed until run 2.
- Markdown docs are Claude's job in both modes. The developer never edits docs or commits by hand.
- Work outside a task stays uncommitted. The developer reviews it in Cursor and runs `/commit`, which makes one commit, and amends it until the push.
- Never push. The developer pushes and opens the PR.
- The developer is the only author: commits and PR descriptions carry no `Co-Authored-By` trailer and no "Generated with Claude Code" line.

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
