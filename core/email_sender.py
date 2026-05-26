"""Postmark transactional email — the outbound channel for auth flows.

Wraps Postmark's REST API with a tiny send_email() helper plus
named convenience functions for each transactional template
(verify, password reset, password changed, email changed, etc.).

Reads POSTMARK_SERVER_TOKEN from env. The token must be set in the
container's env_file (we added it to ~/swarm/ionic/.env in the
2026-05-22 ops setup).

All emails are sent via the Default Transactional Stream
("outbound" MessageStream). Account is still in Postmark's review
state at the moment; until that clears, sends only deliver to
verified Sender Signature addresses (your own gmail) — but the
API still returns 200 OK and the send is queued. Useful for tests.

Public API:

    send_email(to, subject, text_body, html_body=None, ...) -> dict
    send_verify_email(to, verify_url) -> dict
    send_password_reset_email(to, reset_url) -> dict
    send_password_changed_notification(to) -> dict
    send_email_changed_notification(to, new_email) -> dict
    send_oanda_key_revoked_email(to, error_message) -> dict

Each returns a dict shaped like:

    {
        "ok":             bool,
        "message_id":     str | None,
        "submitted_at":   ISO timestamp string | None,
        "error":          short description if not ok,
        "error_code":     Postmark's numeric error code if available,
    }

NEVER raises on Postmark errors — caller decides how to handle.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


POSTMARK_API_URL = "https://api.postmarkapp.com/email"
POSTMARK_TIMEOUT_SEC = 10

# Defaults per SAAS_AUTH_PLAN.md
DEFAULT_FROM     = os.getenv("POSTMARK_FROM",      "noreply@foundationbots.com")
DEFAULT_REPLY_TO = os.getenv("POSTMARK_REPLY_TO",  "support@foundationbots.com")
DEFAULT_STREAM   = "outbound"

# Base URL for links in emails. Falls back to apex if not set.
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://ionic.foundationbots.com")


# ─── Core send function ────────────────────────────────────────────────────


def send_email(
    to: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
    *,
    from_addr: str = DEFAULT_FROM,
    reply_to: str = DEFAULT_REPLY_TO,
    message_stream: str = DEFAULT_STREAM,
    tag: Optional[str] = None,
) -> dict:
    """Send a single transactional email via Postmark.

    Returns a result dict (see module docstring). Never raises.
    """
    # Bug-hunt suppression — see Corinthian's email_sender for rationale.
    # Stops the TestUser harness from burning Postmark's monthly quota.
    if (os.environ.get("BUGHUNT_BYPASS_RATELIMIT") == "1"
            and to and to.startswith("bughunt+")):
        logger.info(f"[bughunt suppressed] send_email to={to} subject={subject!r}")
        return {
            "ok": True, "message_id": "bughunt-suppressed", "submitted_at": None,
            "error": None,
        }

    token = os.environ.get("POSTMARK_SERVER_TOKEN")
    if not token:
        logger.error("POSTMARK_SERVER_TOKEN not set — cannot send email")
        return {
            "ok": False, "message_id": None, "submitted_at": None,
            "error": "POSTMARK_SERVER_TOKEN not configured",
        }
    if not to or "@" not in to:
        return {
            "ok": False, "message_id": None, "submitted_at": None,
            "error": "invalid recipient address",
        }

    body = {
        "From":          from_addr,
        "To":            to,
        "ReplyTo":       reply_to,
        "Subject":       subject,
        "TextBody":      text_body,
        "MessageStream": message_stream,
    }
    if html_body:
        body["HtmlBody"] = html_body
    if tag:
        body["Tag"] = tag

    try:
        resp = requests.post(
            POSTMARK_API_URL,
            json=body,
            headers={
                "Accept":                  "application/json",
                "Content-Type":            "application/json",
                "X-Postmark-Server-Token": token,
            },
            timeout=POSTMARK_TIMEOUT_SEC,
        )
    except requests.Timeout:
        return {"ok": False, "message_id": None, "submitted_at": None, "error": "postmark timeout"}
    except requests.RequestException as exc:
        return {"ok": False, "message_id": None, "submitted_at": None,
                "error": f"network: {type(exc).__name__}"}

    try:
        payload = resp.json()
    except ValueError:
        return {"ok": False, "message_id": None, "submitted_at": None,
                "error": f"non-json response (status {resp.status_code})"}

    if resp.status_code == 200 and payload.get("ErrorCode") == 0:
        return {
            "ok":           True,
            "message_id":   payload.get("MessageID"),
            "submitted_at": payload.get("SubmittedAt"),
        }

    return {
        "ok":           False,
        "message_id":   None,
        "submitted_at": None,
        "error":        payload.get("Message", f"HTTP {resp.status_code}"),
        "error_code":   payload.get("ErrorCode"),
    }


# ─── Template helpers ──────────────────────────────────────────────────────


def send_verify_email(to: str, token: str, *, base_url: str = APP_BASE_URL) -> dict:
    verify_url = f"{base_url}/verify?token={token}"
    return send_email(
        to=to,
        subject="Confirm your Foundation account",
        text_body=_VERIFY_TEXT.format(verify_url=verify_url),
        html_body=_VERIFY_HTML.format(verify_url=verify_url),
        tag="verify-email",
    )


def send_password_reset_email(to: str, token: str, *, base_url: str = APP_BASE_URL) -> dict:
    reset_url = f"{base_url}/reset-password?token={token}"
    return send_email(
        to=to,
        subject="Reset your Foundation password",
        text_body=_RESET_TEXT.format(reset_url=reset_url),
        html_body=_RESET_HTML.format(reset_url=reset_url),
        tag="password-reset",
    )


def send_password_changed_notification(to: str) -> dict:
    """Sent AFTER a successful password change. Security-best-practice:
    notify the account holder that their password just changed so they
    can act if they didn't do it.
    """
    return send_email(
        to=to,
        subject="Your Foundation password was changed",
        text_body=_PASSWORD_CHANGED_TEXT,
        html_body=_PASSWORD_CHANGED_HTML,
        tag="password-changed",
    )


def send_email_changed_notification(old_email: str, new_email: str) -> dict:
    """Sent to the OLD email address when a user changes their email.
    Standard SaaS security practice — even if a bad actor gained
    access and is trying to lock out the legitimate owner, the OLD
    address gets warned.
    """
    return send_email(
        to=old_email,
        subject="Your Foundation email was changed",
        text_body=_EMAIL_CHANGED_TEXT.format(new_email=new_email),
        html_body=_EMAIL_CHANGED_HTML.format(new_email=new_email),
        tag="email-changed",
    )


def send_oanda_key_revoked_email(to: str, error_message: str) -> dict:
    """Sent when the engine detects EAPI:Invalid key from Oanda and
    auto-flips the user back to shadow mode. Per SAAS_BYOK_PLAN.md.
    """
    return send_email(
        to=to,
        subject="Your Oanda connection stopped working",
        text_body=_OANDA_REVOKED_TEXT.format(error=error_message),
        html_body=_OANDA_REVOKED_HTML.format(error=error_message),
        tag="oanda-key-revoked",
    )


def send_live_mode_activated_email(to: str) -> dict:
    """Sent when a user flips from shadow to live mode."""
    return send_email(
        to=to,
        subject="You're now trading live with Foundation",
        text_body=_LIVE_MODE_TEXT,
        html_body=_LIVE_MODE_HTML,
        tag="live-mode-activated",
    )


def send_live_mode_confirmation_code(to: str, code: str) -> dict:
    """Sent BEFORE a shadow→live flip. User must enter the code on
    the Mode page to actually flip. Last "are you sure" before real
    Oanda orders start flowing."""
    text = _LIVE_MODE_CONFIRM_CODE_TEXT.format(code=code)
    html = _LIVE_MODE_CONFIRM_CODE_HTML.format(code=code)
    return send_email(
        to=to,
        subject=f"Foundation confirmation code: {code}",
        text_body=text,
        html_body=html,
        tag="live-mode-confirm-code",
    )


# ─── Template bodies ───────────────────────────────────────────────────────
# Plain-text and HTML versions of every transactional email. Kept
# minimal — no images (deliverability), no fancy CSS (each inbox
# renders differently), no marketing language.

_VERIFY_TEXT = """Welcome to Foundation.

To confirm your email and activate your account, click the link below:

  {verify_url}

This link expires in 24 hours.

If you didn't sign up for Foundation, you can safely ignore this email — no account
will be activated without confirmation.

—
Foundation · foundationbots.com
Reply to this email if you need help. We read every message.
"""

_VERIFY_HTML = """<!DOCTYPE html>
<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a1a; line-height: 1.6;">
<h2 style="font-size: 20px; margin: 0 0 16px;">Welcome to Foundation.</h2>
<p>To confirm your email and activate your account, click below:</p>
<p style="margin: 24px 0;"><a href="{verify_url}" style="display: inline-block; padding: 12px 24px; background: #d4af37; color: #1a1a1a; text-decoration: none; border-radius: 6px; font-weight: 600;">Confirm my email</a></p>
<p style="font-size: 13px; color: #666;">Or paste this link into your browser: <br><span style="font-family: monospace; font-size: 12px; word-break: break-all;">{verify_url}</span></p>
<p style="font-size: 13px; color: #666;">This link expires in 24 hours.</p>
<p style="font-size: 13px; color: #666;">If you didn't sign up for Foundation, you can safely ignore this email — no account will be activated without confirmation.</p>
<hr style="border: none; border-top: 1px solid #e0e0e0; margin: 32px 0 16px;">
<p style="font-size: 12px; color: #888;">Foundation · <a href="https://foundationbots.com" style="color: #888;">foundationbots.com</a><br>Reply to this email if you need help. We read every message.</p>
</body></html>
"""

_RESET_TEXT = """Someone (hopefully you) requested a password reset for your Foundation
account. To set a new password, click the link below:

  {reset_url}

This link expires in 1 hour.

If you didn't request this, you can ignore this email — your password
hasn't been changed.

—
Foundation · foundationbots.com
Reply to this email if you need help.
"""

_RESET_HTML = """<!DOCTYPE html>
<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a1a; line-height: 1.6;">
<h2 style="font-size: 20px; margin: 0 0 16px;">Reset your password</h2>
<p>Someone (hopefully you) requested a password reset for your Foundation account.</p>
<p style="margin: 24px 0;"><a href="{reset_url}" style="display: inline-block; padding: 12px 24px; background: #d4af37; color: #1a1a1a; text-decoration: none; border-radius: 6px; font-weight: 600;">Set a new password</a></p>
<p style="font-size: 13px; color: #666;">Or paste this link: <br><span style="font-family: monospace; font-size: 12px; word-break: break-all;">{reset_url}</span></p>
<p style="font-size: 13px; color: #666;">This link expires in 1 hour.</p>
<p style="font-size: 13px; color: #666;">If you didn't request this, you can ignore this email — your password hasn't been changed.</p>
<hr style="border: none; border-top: 1px solid #e0e0e0; margin: 32px 0 16px;">
<p style="font-size: 12px; color: #888;">Foundation · <a href="https://foundationbots.com" style="color: #888;">foundationbots.com</a></p>
</body></html>
"""

_PASSWORD_CHANGED_TEXT = """Your Foundation password was just changed.

If this was you, you can ignore this email.

If this wasn't you, your account may be compromised. Reply to this
email immediately so we can help you secure it.

—
Foundation · foundationbots.com
"""

_PASSWORD_CHANGED_HTML = """<!DOCTYPE html>
<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a1a; line-height: 1.6;">
<h2 style="font-size: 20px; margin: 0 0 16px;">Your Foundation password was changed</h2>
<p>If this was you, you can ignore this email.</p>
<p style="background: #fff3cd; padding: 12px 16px; border-left: 3px solid #d4af37;"><strong>If this wasn't you</strong>, your account may be compromised. Reply to this email immediately so we can help you secure it.</p>
<hr style="border: none; border-top: 1px solid #e0e0e0; margin: 32px 0 16px;">
<p style="font-size: 12px; color: #888;">Foundation · <a href="https://foundationbots.com" style="color: #888;">foundationbots.com</a></p>
</body></html>
"""

_EMAIL_CHANGED_TEXT = """Your Foundation email was changed to {new_email}.

If this was you, you'll need to verify the new address before logging in
with it. You can ignore this email.

If this wasn't you, your account is compromised. Reply to this email
immediately so we can help you regain control. Your old email
({{this email}}) still works for support.

—
Foundation · foundationbots.com
"""

_EMAIL_CHANGED_HTML = """<!DOCTYPE html>
<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a1a; line-height: 1.6;">
<h2 style="font-size: 20px; margin: 0 0 16px;">Your Foundation email was changed</h2>
<p>Your Foundation account email was changed to <strong>{new_email}</strong>.</p>
<p style="background: #fff3cd; padding: 12px 16px; border-left: 3px solid #d4af37;"><strong>If this wasn't you</strong>, your account is compromised. Reply to this email immediately so we can help you regain control. This old address still works for support.</p>
<hr style="border: none; border-top: 1px solid #e0e0e0; margin: 32px 0 16px;">
<p style="font-size: 12px; color: #888;">Foundation · <a href="https://foundationbots.com" style="color: #888;">foundationbots.com</a></p>
</body></html>
"""

_OANDA_REVOKED_TEXT = """Foundation tried to place a trade for you and Oanda rejected
your API key ({error}).

I've switched your bot back to shadow (paper) mode automatically —
no real money trades will happen until you reconnect a working
Oanda key.

To restore live trading:
  1. Check your Oanda account for any active API keys
  2. If revoked, generate a new key with trading permission
  3. Paste it into Foundation Settings → Oanda Connection

Your bot keeps tracking signals in shadow mode while you sort this
out — no missed trades, just no real execution.

—
Foundation · foundationbots.com
Reply if you need help. We read every message.
"""

_OANDA_REVOKED_HTML = """<!DOCTYPE html>
<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a1a; line-height: 1.6;">
<h2 style="font-size: 20px; margin: 0 0 16px;">Your Oanda connection stopped working</h2>
<p>Foundation tried to place a trade for you and Oanda rejected your API key (<code>{error}</code>).</p>
<p>I've switched your bot back to shadow (paper) mode automatically — no real money trades will happen until you reconnect a working Oanda key.</p>
<h3 style="font-size: 16px;">To restore live trading:</h3>
<ol>
<li>Check your Oanda account for any active API keys</li>
<li>If revoked, generate a new key with trading permission</li>
<li>Paste it into Foundation Settings → Oanda Connection</li>
</ol>
<p>Your bot keeps tracking signals in shadow mode while you sort this out — no missed trades, just no real execution.</p>
<hr style="border: none; border-top: 1px solid #e0e0e0; margin: 32px 0 16px;">
<p style="font-size: 12px; color: #888;">Foundation · <a href="https://foundationbots.com" style="color: #888;">foundationbots.com</a><br>Reply if you need help. We read every message.</p>
</body></html>
"""

_LIVE_MODE_TEXT = """Your Foundation account just flipped to live mode.

From here:
  - The next time your bot enters a position, real orders are placed
    on your Oanda account using the API key you provided.
  - All other safeguards remain — paradigm consensus, AI verdict gate,
    drawdown halts, position size caps.
  - To revert to shadow (paper) at any time, go to Mode → Switch to Shadow.

If this wasn't you, change your Foundation password immediately and
revoke the Oanda key in your Oanda account.

—
Foundation · foundationbots.com
"""

_LIVE_MODE_HTML = """<!DOCTYPE html>
<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a1a; line-height: 1.6;">
<h2 style="font-size: 20px; margin: 0 0 16px;">You're now trading live with Foundation</h2>
<p>Your Foundation account just flipped to <strong>live mode</strong>.</p>
<p>From here:</p>
<ul>
<li>The next time your bot enters a position, real orders are placed on your Oanda account.</li>
<li>All other safeguards remain — paradigm consensus, AI verdict gate, drawdown halts, position size caps.</li>
<li>To revert to shadow (paper) at any time, go to Mode → Switch to Shadow.</li>
</ul>
<p style="background: #fff3cd; padding: 12px 16px; border-left: 3px solid #d4af37;"><strong>If this wasn't you</strong>, change your Foundation password immediately and revoke the Oanda key in your Oanda account.</p>
<hr style="border: none; border-top: 1px solid #e0e0e0; margin: 32px 0 16px;">
<p style="font-size: 12px; color: #888;">Foundation · <a href="https://foundationbots.com" style="color: #888;">foundationbots.com</a></p>
</body></html>
"""


_LIVE_MODE_CONFIRM_CODE_TEXT = """You requested to flip your Foundation account to LIVE trading.

Your confirmation code is:

  {code}

Enter this code on the Mode page within 15 minutes to complete the
switch. Once you confirm, real Oanda orders will start flowing on the
next trade entry your bot identifies.

If you DIDN'T request this:
  - Do NOT enter the code anywhere.
  - Change your Foundation password immediately.
  - Email support@foundationbots.com.

Your bot stays in shadow mode until you enter the code.

—
Foundation · foundationbots.com
"""


_LIVE_MODE_CONFIRM_CODE_HTML = """<!DOCTYPE html>
<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a1a; line-height: 1.6;">
<h2 style="font-size: 20px; margin: 0 0 16px;">Confirm your switch to LIVE trading</h2>
<p>You requested to flip your Foundation account to <strong>live mode</strong>.</p>
<p>Your confirmation code is:</p>
<p style="font-size: 32px; font-weight: bold; letter-spacing: 8px; text-align: center; background: #f6f6f6; padding: 20px; border-radius: 6px; font-family: monospace; color: #1a1a1a;">{code}</p>
<p>Enter this code on the <strong>Mode</strong> page within <strong>15 minutes</strong> to complete the switch. Once you confirm, real Oanda orders will start flowing on the next trade entry your bot identifies.</p>
<p style="background: #fff3cd; padding: 12px 16px; border-left: 3px solid #d4af37;"><strong>If you didn't request this</strong>, do NOT enter the code anywhere. Change your Foundation password immediately and email <a href="mailto:support@foundationbots.com" style="color: #c8922a;">support@foundationbots.com</a>. Your bot stays in shadow mode until the code is entered.</p>
<hr style="border: none; border-top: 1px solid #e0e0e0; margin: 32px 0 16px;">
<p style="font-size: 12px; color: #888;">Foundation · <a href="https://foundationbots.com" style="color: #888;">foundationbots.com</a></p>
</body></html>
"""
