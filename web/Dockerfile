# syntax=docker/dockerfile:1.7
#
# Anton web — React + Vite SPA fronted by nginx.
# Multi-stage: stage 1 compiles the bundle with Node, stage 2 serves the
# resulting static dist/ from a slim nginx image. The runtime image carries
# no Node toolchain — just nginx + ~3MB of static assets.
#
# Build context: this directory (web/), not the repo root.

# ─── Stage 1: build the React bundle ────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

# Copy lockfile first so adding/removing app code doesn't bust the npm layer.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# Anti-regression lint: fail the build if a stale single-tenant localStorage
# token key sneaks back in. See scripts/check-token-key.sh for the bug history.
RUN sh scripts/check-token-key.sh
# Vite reads VITE_* env vars at build time. If you need to bake API URLs into
# the bundle, pass them as build args here. For now the SPA resolves /api and
# /ws against window.location — nginx (next stage) proxies those to anton-api.
RUN npm run build


# ─── Stage 2: nginx serves dist/ + reverse-proxies /api and /ws ────────────
FROM nginx:1.27-alpine AS runtime

# Drop the default vhost and replace with ours.
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/anton.conf

COPY --from=build /app/dist /usr/share/nginx/html

# nginx:alpine listens on 80 by default. The edge proxy (Traefik / Caddy /
# whatever the swarm runs) terminates TLS and forwards to this port over
# the internal docker network.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O- http://127.0.0.1/ >/dev/null || exit 1
