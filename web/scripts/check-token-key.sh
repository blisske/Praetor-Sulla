#!/bin/sh
# Build-time lint: fail if any localStorage key matches a legacy single-tenant
# bot token name. The SaaS auth convention (api/auth.py) issues a 'token' key
# with no prefix; auth.jsx, DemoMode.jsx, and Signup.jsx all read it as 'token'.
#
# Bug history: when a bot was ported to the SaaS multi-tenant pattern,
# web/src/lib/api.js + pages/Dashboard.jsx sometimes kept their LEGACY
# single-tenant localStorage keys (doric_token, ionic_token, etc.) while the
# new auth.jsx + DemoMode.jsx + Signup.jsx — copied from Corinthian during
# the port — used 'token'. Mismatch → every API call lands without an
# Authorization header → 401 → axios interceptor bounces to /login → user
# sees "dashboard for a split second then login again" loop.
# See Doric commit 3df0f26 / Ionic commit 35f2127 for the original incidents.
#
# Run from web/ (Dockerfile WORKDIR=/app, the build context is web/).
set -e

STALE=$(grep -rnE "localStorage\.(getItem|setItem|removeItem)\( *['\"](corinthian_token|doric_token|ionic_token|sulla_token|anton_token|tiberius_token)['\"]" src/ 2>/dev/null || true)

if [ -n "$STALE" ]; then
  echo "❌ Build failed: stale bot-specific localStorage token key found."
  echo "   The SaaS auth convention is localStorage 'token' (no prefix)."
  echo "   See Doric commit 3df0f26 / Ionic commit 35f2127 for the bug history."
  echo ""
  echo "$STALE"
  exit 1
fi
