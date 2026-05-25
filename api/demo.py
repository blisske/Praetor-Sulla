"""FastAPI router for the public-no-auth demo mode.

This router has ONE job: mint a short-lived JWT for the demo user
(user_id=2 by default; override via DEMO_USER_ID env) that any visitor
can grab without credentials. The minted token carries an `is_demo: true`
claim so the frontend can recognize demo sessions and gate write actions.

Once the visitor has the token, they use the regular /api/* endpoints
exactly like a logged-in user — get_db() in api/main.py already serves
demo_data.db for the demo user when shadow_mode is off (legacy carve-
out preserved through the SaaS cutover).

Why a public endpoint instead of just letting users log in as demo
with username + password? The demo password is bcrypt-hashed in .env;
there's no plaintext to publish. This endpoint trades a known IP-based
rate-limited request for a token, no credentials needed.

Endpoints:
  POST /api/demo/login    Mint a demo JWT (no auth required, rate-limited).

The frontend's /demo route auto-POSTs to this endpoint on mount, stores
the returned token, and redirects the visitor to / with the dashboard
already populated.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from shared import auth as core_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo", tags=["demo"])


# Configurable target user. Defaults to user_id=2 (the demo user seeded
# during the SaaS cutover migration). Operator can override if they
# re-id the demo account later.
DEMO_USER_ID = int(os.getenv("DEMO_USER_ID", "2"))

# Demo tokens are deliberately SHORT-LIVED. A casual visitor only needs
# enough time to click through the dashboard; a real user signs up. 24h
# is plenty for someone exploring the demo over a weekend without
# leaving long-lived public tokens lying around.
DEMO_TOKEN_HOURS = int(os.getenv("DEMO_TOKEN_HOURS", "24"))


# Demo-login rate limit — env-tunable so the operator can loosen this
# during a promo (or tighten during abuse) without a code change.
#
# Defaults are generous on purpose:
# - 100/hr/IP handles a small office or shared NAT where multiple
#   curious visitors come from the same egress (mobile carrier NAT,
#   conference WiFi, household router). Still blunts automated token
#   scrapers, which would hit the limit within seconds.
# - Each token is good for 24h, so legitimate users rarely need to
#   re-mint within the window.
#
# Tune via env in ~/swarm/ionic/.env (then restart ionic-api):
#   DEMO_LOGIN_RATE_LIMIT=200            # max attempts per window per IP
#   DEMO_LOGIN_RATE_WINDOW_SEC=3600      # window length in seconds
#
# The limiter is per-process (api container), so an api restart clears
# all in-flight counters — handy as a manual escape hatch.
DEMO_LOGIN_RATE_LIMIT      = int(os.getenv("DEMO_LOGIN_RATE_LIMIT",      "100"))
DEMO_LOGIN_RATE_WINDOW_SEC = int(os.getenv("DEMO_LOGIN_RATE_WINDOW_SEC", "3600"))

demo_login_limiter = core_auth.RateLimiter(
    max_attempts = DEMO_LOGIN_RATE_LIMIT,
    window_sec   = DEMO_LOGIN_RATE_WINDOW_SEC,
)


class DemoTokenResponse(BaseModel):
    token: str
    user:  dict
    is_demo: bool = True


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=DemoTokenResponse)
async def demo_login(request: Request):
    """Mint a demo JWT — no credentials required, IP-rate-limited.

    The token has `is_demo: true` in its claims. Frontend uses that to
    gate write controls (Restart Engine, Save Config, account deletion,
    etc.) so casual demo visitors can browse but can't break anything.

    Returns 503 if the demo user doesn't exist in global.db (operator
    hasn't run the migration script yet). Returns 429 on rate-limit.
    """
    ip = _client_ip(request)
    if not demo_login_limiter.allow(f"ip:{ip}"):
        retry = demo_login_limiter.retry_after(f"ip:{ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many demo logins from this network. Please wait.",
            headers={"Retry-After": str(retry)},
        )

    user = core_auth.get_user_by_id(DEMO_USER_ID)
    if not user:
        logger.error(
            f"Demo login requested but DEMO_USER_ID={DEMO_USER_ID} is missing "
            "from global.db. Operator must seed the demo user via the "
            "migration script."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo mode is unavailable right now. Please try again later.",
        )

    token = core_auth.create_jwt(
        user_id        = user.id,
        email          = user.email,
        email_verified = user.email_verified,
        is_admin       = user.is_admin,
        expires_delta  = timedelta(hours=DEMO_TOKEN_HOURS),
        extra_claims   = {"is_demo": True},
    )

    logger.info(f"Demo login from ip={ip} → user_id={user.id} ({user.email})")

    return DemoTokenResponse(
        token = token,
        user  = {
            "id":             user.id,
            "email":          user.email,
            "email_verified": user.email_verified,
            "is_admin":       user.is_admin,
        },
        is_demo = True,
    )
