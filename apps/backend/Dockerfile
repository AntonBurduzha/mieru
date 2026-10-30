# syntax=docker/dockerfile:1

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
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# runtime stage
FROM python:3.13-slim
RUN groupadd --system --gid 10001 mieru \
  && useradd --system --uid 10001 --gid 10001 --no-create-home mieru
WORKDIR /app

COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000 8001

ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA
CMD ["uvicorn", "mieru.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
