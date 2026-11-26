# syntax=docker/dockerfile:1

# The tracking script, which ingest serves at /js/p.js
FROM node:24-slim AS tracker
RUN npm install --global pnpm@12.11.2
WORKDIR /repo/apps/tracker
COPY apps/tracker/package.json apps/tracker/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/pnpm-store \
    pnpm install --frozen-lockfile --store-dir /pnpm-store
COPY apps/tracker/build.mjs ./
COPY apps/tracker/src ./src
RUN node build.mjs

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# dependencies only, layer is reused until pyproject.toml or uv.lock change
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=apps/backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=apps/backend/uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project

# project itself
COPY apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/README.md ./
COPY apps/backend/src ./src
# Into the package before it's built: the wheel takes every file under src/mieru
COPY --from=tracker /repo/apps/tracker/dist/p.js ./src/mieru/ingest/static/p.js
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# runtime stage
FROM python:3.13-slim
RUN groupadd --system --gid 10001 mieru \
  && useradd --system --uid 10001 --gid 10001 --no-create-home mieru
WORKDIR /app

COPY --from=build /app/.venv /app/.venv
# Migrations: one-off containers run them before each deploy.
# Postgres: the Alembic config and scripts. ClickHouse: the SQL files for ch_migrate
COPY apps/backend/alembic.ini ./
COPY apps/backend/migrations ./migrations
COPY apps/backend/clickhouse ./clickhouse
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000 8001

ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA
CMD ["uvicorn", "--factory", "mieru.api.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
