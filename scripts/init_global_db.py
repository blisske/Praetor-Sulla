#!/usr/bin/env python3
"""Initialize ~/swarm/ionic/data/global.db with the multi-tenant schema.

Creates the shared SaaS-tier tables that live alongside per-user
ionic.db files:

  - users                 (auth identity)
  - broker_keys           (encrypted Oanda/Oanda/Oanda credentials)
  - email_verifications   (single-use 24h tokens for signup verify)
  - password_resets       (single-use 1h tokens for password reset)
  - broker_key_events     (append-only audit trail of key-touching events)

Plus copies the existing single-tenant `login_attempts` and `market_states`
tables INTO global.db so they become the shared versions. (Original tables
in ionic.db will be left for the migration script to deal with —
this init script ONLY creates global.db.)

Idempotent: safe to re-run. CREATE TABLE IF NOT EXISTS + ALTER TABLE-style
column adds. Will NOT overwrite existing data.

Usage:
    docker exec ionic-api python3 /app/scripts/init_global_db.py
    # or, locally during dev:
    cd ~/swarm/ionic/repo && python3 scripts/init_global_db.py

See SAAS_SCHEMA_PLAN.md and SAAS_AUTH_PLAN.md for schema rationale.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


# Default path — overridable via --db-path or GLOBAL_DB_PATH env
DEFAULT_DB_PATH = "/home/blisske/swarm/ionic/data/global.db"


SCHEMA_STATEMENTS = [
    # ── users ──────────────────────────────────────────────────────────
    # The auth identity table. Operator (blisske) becomes id=1 during the
    # migration. Soft-delete via deleted_at; user_id is never reused.
    """
    CREATE TABLE IF NOT EXISTS users (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        email             TEXT NOT NULL,
        password_hash     TEXT NOT NULL,
        email_verified    INTEGER DEFAULT 0,        -- 0 or 1
        is_admin          INTEGER DEFAULT 0,        -- operator only at launch
        created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_login_at     DATETIME,
        deleted_at        DATETIME,                 -- soft-delete; never NULL after deletion
        recovery_email    TEXT,                     -- v1.1 optional; null at v1 launch
        totp_secret       TEXT,                     -- base32-encoded shared secret; null = 2FA off
        totp_enrolled_at  DATETIME                  -- set on successful enrollment-confirm; null = not enrolled (even if totp_secret set during pending enrollment)
    )
    """,
    # Case-insensitive uniqueness — only enforced on non-deleted users.
    # SQLite supports COLLATE NOCASE on columns; we use that + a partial
    # index keyed on deleted_at IS NULL.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_active
    ON users (email COLLATE NOCASE)
    WHERE deleted_at IS NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_recovery_email_active
    ON users (recovery_email COLLATE NOCASE)
    WHERE recovery_email IS NOT NULL AND deleted_at IS NULL
    """,

    # ── broker_keys ────────────────────────────────────────────────────
    # Encrypted per-user broker credentials. Key & secret encrypted with
    # AES-256-GCM using BROKER_KEY_MASTER. UNIQUE(user_id, broker) means
    # one key per (user, broker) — rotation overwrites via UPSERT.
    """
    CREATE TABLE IF NOT EXISTS broker_keys (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL,
        broker          TEXT NOT NULL,            -- 'oanda' (v1), 'oanda', 'oanda'
        key_enc         TEXT NOT NULL,            -- base64(iv || ciphertext_with_tag)
        secret_enc      TEXT,                     -- nullable for brokers without split secret
        scope           TEXT NOT NULL,            -- 'read' | 'trade' | 'unknown'
        validated_at    DATETIME,
        last_used_at    DATETIME,
        last_error      TEXT,                     -- e.g. "EAPI:Invalid key"
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_broker_keys_user_broker
    ON broker_keys (user_id, broker)
    """,

    # ── email_verifications ────────────────────────────────────────────
    # Single-use 32-byte URL-safe random tokens. 24h expiry. consumed_at
    # set on first use; null otherwise. Cleanup of consumed/expired tokens
    # is the API's responsibility on each insert (best-effort).
    """
    CREATE TABLE IF NOT EXISTS email_verifications (
        token           TEXT PRIMARY KEY,
        user_id         INTEGER NOT NULL,
        expires_at      DATETIME NOT NULL,
        consumed_at     DATETIME,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_email_verifications_user_active
    ON email_verifications (user_id)
    WHERE consumed_at IS NULL
    """,

    # ── password_resets ────────────────────────────────────────────────
    # Same shape as email_verifications; 1h expiry.
    """
    CREATE TABLE IF NOT EXISTS password_resets (
        token           TEXT PRIMARY KEY,
        user_id         INTEGER NOT NULL,
        expires_at      DATETIME NOT NULL,
        consumed_at     DATETIME,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_password_resets_user_active
    ON password_resets (user_id)
    WHERE consumed_at IS NULL
    """,

    # ── tos_acceptances ───────────────────────────────────────────────
    # Audit trail of which Terms-of-Service version each user accepted
    # + when + from where. One row per acceptance (signup + every re-
    # accept after a version bump). The CURRENT version constant lives
    # in core.auth (CURRENT_TOS_VERSION) — bumping that string forces
    # the frontend to surface a re-acceptance prompt on next login.
    """
    CREATE TABLE IF NOT EXISTS tos_acceptances (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        tos_version  TEXT NOT NULL,
        accepted_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        ip           TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tos_acceptances_user
    ON tos_acceptances (user_id, id DESC)
    """,

    # ── live_mode_confirmations ───────────────────────────────────────
    # 6-digit OTP codes for confirming the shadow→live flip. Acts as
    # one last "are you sure" check before real Oanda orders start
    # flowing. 15-minute expiry. attempts_remaining decrements on each
    # wrong-code submit (drops to 0 → invalidated).
    """
    CREATE TABLE IF NOT EXISTS live_mode_confirmations (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id             INTEGER NOT NULL,
        code                TEXT NOT NULL,
        expires_at          DATETIME NOT NULL,
        consumed_at         DATETIME,
        attempts_remaining  INTEGER NOT NULL DEFAULT 5,
        created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_live_mode_confirmations_user_active
    ON live_mode_confirmations (user_id)
    WHERE consumed_at IS NULL
    """,

    # ── totp_recovery_codes ───────────────────────────────────────────
    # 10 single-use 8-character backup codes per TOTP enrollment. Hashed
    # at rest (argon2 via passlib — same scheme as passwords) so a DB
    # leak doesn't give recovery codes in cleartext. used_at flips on
    # consumption; never reused. Regenerating wipes all unused codes
    # for the user and inserts a fresh 10.
    """
    CREATE TABLE IF NOT EXISTS totp_recovery_codes (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL,
        code_hash       TEXT NOT NULL,
        used_at         DATETIME,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_totp_recovery_codes_user_active
    ON totp_recovery_codes (user_id)
    WHERE used_at IS NULL
    """,

    # ── broker_key_events ──────────────────────────────────────────────
    # Append-only audit trail. Controlled vocabulary in event_type:
    #   key_added | key_rotated | key_disconnected
    #   key_validation_failed
    #   scope_detected_read | scope_detected_trade | scope_detected_unknown
    #   key_revoked_by_broker
    #   mode_flipped_to_live | mode_flipped_to_shadow
    """
    CREATE TABLE IF NOT EXISTS broker_key_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
        user_id         INTEGER NOT NULL,
        broker          TEXT NOT NULL,
        event_type      TEXT NOT NULL,
        detail          TEXT,
        ip              TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_broker_key_events_user
    ON broker_key_events (user_id, timestamp DESC)
    """,

    # ── login_attempts ─────────────────────────────────────────────────
    # Migrated from per-bot scope to global. Used for brute-force
    # detection across the entire user base.
    """
    CREATE TABLE IF NOT EXISTS login_attempts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
        username        TEXT,
        ip              TEXT,
        result          TEXT                      -- 'success', 'wrong_password', 'no_such_user'
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_login_attempts_timestamp
    ON login_attempts (timestamp DESC)
    """,

    # ── market_states ──────────────────────────────────────────────────
    # Shared market data — all per-user engines upsert here.
    # See SAAS_SCHEMA_PLAN.md for the upsert rationale.
    """
    CREATE TABLE IF NOT EXISTS market_states (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
        symbol          TEXT,
        price           REAL,
        adx             REAL,
        regime          TEXT,
        trend           TEXT,
        rsi             REAL,
        volume          REAL,
        avg_volume      REAL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_market_states_symbol_ts
    ON market_states (symbol, timestamp)
    """,
]


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Idempotently add a column to an existing table.

    Uses PRAGMA table_info to check first instead of relying on the
    ALTER TABLE error path (which would also fail on locking issues etc.
    and conflate them with "already exists"). Safe to run on every
    init_global_db() call.
    """
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing_cols = {r[1] for r in rows}
    if column in existing_cols:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_global_db(db_path: str, verbose: bool = True) -> None:
    """Create or update the schema at the given path.

    Safe to re-run. Will not modify or remove existing data.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    is_new = not path.exists()
    if verbose:
        print(f"{'Creating' if is_new else 'Updating'} {path}")

    conn = sqlite3.connect(str(path))
    try:
        # WAL mode for concurrent reader/writer access — matches the
        # per-user ionic.db pattern
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")

        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)

        # ── In-place migrations for existing DBs ──────────────────────
        # CREATE TABLE IF NOT EXISTS only creates tables that don't yet
        # exist; it does NOT add new columns to existing tables. Add new
        # users.* columns idempotently — ALTER TABLE ADD COLUMN errors
        # cleanly if the column already exists, so we swallow that case.
        _add_column_if_missing(conn, "users", "totp_secret",      "TEXT")
        _add_column_if_missing(conn, "users", "totp_enrolled_at", "DATETIME")
        conn.commit()

        # Sanity dump
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        tables = [r[0] for r in rows if not r[0].startswith("sqlite_")]
        if verbose:
            print(f"Schema ready. Tables: {', '.join(tables)}")

        # Count rows in each table for the post-init summary
        if verbose:
            for t in tables:
                count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"  {t:<28} {count} rows")

    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize Foundation global.db schema")
    parser.add_argument(
        "--db-path",
        default=os.environ.get("GLOBAL_DB_PATH", DEFAULT_DB_PATH),
        help=f"Path to the global.db file (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress per-table output")
    args = parser.parse_args()

    try:
        init_global_db(args.db_path, verbose=not args.quiet)
    except sqlite3.Error as e:
        print(f"SQLite error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
