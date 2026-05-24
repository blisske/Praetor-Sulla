#!/bin/sh
# Build-time lint: fail if /api/auth/login is registered as a route in
# api/main.py. After the SaaS multi-tenant migration (2026-05-22 for
# Corinthian, 2026-05-23 for Doric/Ionic), this endpoint lives in
# api/auth.py and is mounted via `app.include_router(_auth_router)`.
#
# Re-registering it directly in main.py shadows the SaaS auth flow and
# resurrects the single-tenant env-hash bcrypt path we explicitly retired
# — i.e. the same class of regression that gave us the token-key bug, just
# on the API side. Same anti-regression principle as web/scripts/check-token-key.sh.
#
# Matches FastAPI route decorators (`@<anything>.post("...")`) so docstring/
# comment mentions of "/api/auth/login" don't trip it.
#
# Run from the image's WORKDIR (/app for the api target).
set -e

STALE=$(grep -nE "^[[:space:]]*@[A-Za-z_][A-Za-z0-9_]*\.(post|get|put|delete|patch)\([\"'](/api)?/auth/login" api/main.py 2>/dev/null || true)

if [ -n "$STALE" ]; then
  echo "❌ Build failed: /api/auth/login route registered in api/main.py."
  echo "   This endpoint lives in api/auth.py — mount it via"
  echo "   app.include_router(_auth_router) instead. Re-registering it in"
  echo "   main.py shadows the SaaS multi-tenant auth flow."
  echo ""
  echo "$STALE"
  exit 1
fi
