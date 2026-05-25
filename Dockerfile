# syntax=docker/dockerfile:1.7
#
# Anton — multi-stage Dockerfile.
# One image, two run targets: `engine` (trading daemon + Telegram bot) and
# `api` (FastAPI dashboard backend). They share Python deps and source so
# building both targets reuses 99% of layer cache.
#
# Build:
#   docker build --target engine -t ionic-engine .
#   docker build --target api    -t ionic-api    .
#
# In docker-compose.yml each service points at its target via build.target.

# ─── Base stage: Python + system deps + pip install ─────────────────────────
FROM python:3.12.3-slim-bookworm AS base

# Pin Python interpreter behavior:
#   - PYTHONDONTWRITEBYTECODE: no .pyc litter in writable layer
#   - PYTHONUNBUFFERED: log lines flush immediately so docker logs is live
#   - PIP_NO_CACHE_DIR: don't keep wheel cache after install
#   - PIP_DISABLE_PIP_VERSION_CHECK: skip the noisy version-check call
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata: required for pytz timezone lookups (America/New_York, America/Denver).
# build-essential + libffi-dev + libssl-dev: cffi/coincurve/cryptography wheels
#   are usually prebuilt on linux/amd64, but kept available for future arch
#   shifts (Pi 4, ARM64 VPS) where some packages need a compile fallback.
# curl: lightweight healthcheck against /api/health from inside the api container.
#
# Note: PID-1 / zombie-reaping is handled by docker's built-in tini, injected
# by `init: true` in docker-compose.yml. We deliberately don't `apt-get install
# tini` here — having two tinis (apt's at PID 7, docker's at PID 1) produces
# the noisy "Tini is not running as PID 1" warning on every boot.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tzdata curl ca-certificates \
        build-essential libffi-dev libssl-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first so changing app code doesn't bust the (slow) pip layer.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Now copy the app. .dockerignore excludes venv/, web/node_modules/, *.db
# (except demo_data.db), .git, and other build-context noise.
COPY core/    /app/core/
COPY api/     /app/api/
COPY scripts/ /app/scripts/
COPY docs/    /app/docs/
COPY tests/   /app/tests/

# Non-root user. UID 1000 matches the typical first WSL2 user; bind-mounted
# host paths created with the matching UID are writable without chown gymnastics.
# If your host UID differs, override with --user UID:GID at runtime or rebuild
# with --build-arg APP_UID=...
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid ${APP_GID} ionic \
 && useradd  --uid ${APP_UID} --gid ${APP_GID} --create-home --shell /bin/bash ionic \
 && mkdir -p /app/data \
 && chown -R ionic:ionic /app
USER ionic


# ─── Engine target: trading daemon + Telegram bot ───────────────────────────
FROM base AS engine

# Engine runs from /app/core because its modules import each other relatively
# (config_manager, database, strategy, etc. all live side by side).
WORKDIR /app/core

# Healthcheck: the main loop touches HEARTBEAT_PATH at the top of every
# iteration. Stale beat = frozen daemon = let compose restart us.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD test -f "${HEARTBEAT_PATH:-/app/data/.engine_heartbeat}" \
        && find "${HEARTBEAT_PATH:-/app/data/.engine_heartbeat}" -mmin -10 | grep -q . \
        || exit 1

CMD ["python", "main.py"]


# ─── API target: FastAPI dashboard backend ─────────────────────────────────
FROM base AS api

# Anti-regression lint: fail the build if /api/auth/login is re-registered
# in api/main.py (it lives in api/auth.py post-SaaS migration; re-registering
# would shadow the multi-tenant flow). See scripts/check-no-legacy-login-route.sh.
WORKDIR /app
RUN sh scripts/check-no-legacy-login-route.sh

# API expects to run from its own dir so the `from main import app` in
# uvicorn's app spec resolves to api/main.py.
WORKDIR /app/api

# Healthcheck against the existing /api/health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${AGENT_PORT:-8002}/api/health" || exit 1

# Bind 0.0.0.0 so Docker's bridge-network port publishing actually reaches us.
# AGENT_PORT defaults to 8002 for Ionic (Tiberius=8000, Anton=8001, Ionic=8002).
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${AGENT_PORT:-8002}"]
