# syntax=docker/dockerfile:1
# Strategy Factory v2: dashboard + API + scheduler + research scripts in one image (docs/docker.md).

# --- 1. dashboard (React / Vite) ---------------------------------------------------------------------------
FROM node:20-bookworm-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
# vite build only: the type check runs separately (`docker compose run --rm web-typecheck`), so a type error in
# the dashboard never blocks the image; the Python side does not depend on it.
RUN npx vite build

# --- 2. Python runtime (uv, ADR-0001) ------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 \
    PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 NUMBA_CACHE_DIR=/tmp/numba \
    SF_KILL_FILE=/config/killswitch.json SF_EVENTS_FILE=/config/events.jsonl
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --group web --group dev
COPY scripts ./scripts
COPY tests ./tests
COPY docs ./docs
COPY docker ./docker
COPY --from=web /app/web/dist ./web/dist
RUN useradd -m -u 1000 sf && mkdir -p /config /runs /live /tmp/numba && chown -R sf /config /runs /live /tmp/numba /app
USER sf
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)"
ENTRYPOINT ["python", "/app/docker/entrypoint.py"]
CMD ["web"]
