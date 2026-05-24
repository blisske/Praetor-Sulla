#!/usr/bin/env python3
"""Seed the legacy operator (blisske) as user_id=1 in global.db.

This is the cutover script that has to run BEFORE the new auth router
replaces the single-tenant /api/auth/login. Without it, the dashboard
breaks for the operator the moment the wiring flips, because the new
get_current_user dependency looks for users in global.db.

What it does (idempotent — safe to run repeatedly):

  1. Reads the operator's existing credentials from ~/swarm/ionic/.env:
       API_USERNAME       → becomes the operator's display email (we
                            ALSO require an email override since the env
                            field is usually 'admin', not an email).
       API_PASSWORD_HASH  → reused as-is. The hash is bcrypt; the new
                            auth layer accepts bcrypt and opportunistically
                            re-hashes to argon2 on the next successful
                            login (passlib's deprecated="auto" handles it).
  2. Upserts a row into global.db.users with id=1, the provided email,
     the existing password_hash, email_verified=1 (operator pre-verified),
     is_admin=1. Conflict on email = update in place.
  3. Symlinks /app/data/users/1/Config.yaml → /app/data/Config.yaml so
     the new /api/user/mode endpoint can write through to the live engine
     config without moving the file. The engine keeps reading the same
     path it always has; when the provisioner lands the symlink gets
     replaced with a real file.
  4. Logs the migration to broker_key_events with event_type='operator_seeded'.

What it explicitly does NOT do:
  - Move ionic.db. Engine keeps reading /app/data/ionic.db.
    Per-user DB layout is a provisioner concern, deferred.
  - Seed broker_keys. The operator's existing Oanda creds live in env,
    not in global.db. Wire those in via the BYOK paste endpoint after
    the auth flip, OR continue running them out of env until provisioner
    lands.

Usage (in the ionic-api container):

    docker exec ionic-api python3 /app/scripts/migrate_operator_to_user_1.py \\
        --email blisske@gmail.com

Outside the container (host shell):

    GLOBAL_DB_PATH=~/swarm/foundation/data/global.db \\
    python3 ~/swarm/ionic/repo/scripts/migrate_operator_to_user_1.py \\
        --email blisske@gmail.com

Flags:
  --email EMAIL         (required) Email to seed the operator with. This
                        becomes the username in the new auth flow.
  --password-hash HASH  (optional) Override; defaults to API_PASSWORD_HASH env.
  --db-path PATH        (optional) Override GLOBAL_DB_PATH env / default.
  --user-data-dir DIR   (optional) Override USER_DATA_DIR env / default.
  --config-path PATH    (optional) Override the live Config.yaml path
                        that gets symlinked. Default: /app/data/Config.yaml
                        inside the container, ~/swarm/ionic/data/Config.yaml
                        on the host.
  --dry-run             Print what would happen; touch nothing.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def _detect_default_config_path() -> str:
    """Auto-detect the live Config.yaml path based on whether we're in a container."""
    container_path = "/app/data/Config.yaml"
    host_path = os.path.expanduser("~/swarm/ionic/data/Config.yaml")
    if os.path.exists(container_path):
        return container_path
    return host_path


def _detect_default_db_path() -> str:
    return os.environ.get(
        "GLOBAL_DB_PATH",
        "/app/foundation/global.db" if os.path.exists("/app/data") else
        os.path.expanduser("~/swarm/foundation/data/global.db"),
    )


def _detect_default_user_data_dir() -> str:
    return os.environ.get(
        "USER_DATA_DIR",
        "/app/data/users" if os.path.exists("/app/data") else
        os.path.expanduser("~/swarm/ionic/data/users"),
    )


def upsert_operator(
    *,
    db_path:        str,
    email:          str,
    password_hash:  str,
    dry_run:        bool = False,
) -> int:
    """Insert or update user_id=1 with the operator's credentials.

    Returns the upserted user's id (always 1).
    Raises SystemExit if global.db is missing or schema isn't initialized.
    """
    if not Path(db_path).exists():
        print(f"FATAL: global.db not found at {db_path}", file=sys.stderr)
        print("Run scripts/init_global_db.py first.", file=sys.stderr)
        raise SystemExit(2)

    email_lc = email.strip().lower()

    if dry_run:
        print(f"  [dry-run] Would upsert users.id=1 email={email_lc} hash=<{len(password_hash)}-char>")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    existing = conn.execute("SELECT id, email FROM users WHERE id = 1").fetchone()
    if existing:
        conn.execute(
            "UPDATE users SET email=?, password_hash=?, is_admin=1, email_verified=1, "
            "updated_at=CURRENT_TIMESTAMP, deleted_at=NULL WHERE id=1",
            (email_lc, password_hash),
        )
        print(f"  Updated existing users.id=1 (was email={existing['email']})")
    else:
        # Refuse if some other user_id holds this email
        clash = conn.execute(
            "SELECT id FROM users WHERE email=? AND deleted_at IS NULL",
            (email_lc,),
        ).fetchone()
        if clash and clash["id"] != 1:
            conn.close()
            print(f"FATAL: email {email_lc} already in use by user_id={clash['id']}", file=sys.stderr)
            raise SystemExit(3)
        conn.execute(
            "INSERT INTO users (id, email, password_hash, is_admin, email_verified) "
            "VALUES (1, ?, ?, 1, 1)",
            (email_lc, password_hash),
        )
        # Bump sqlite_sequence so the next new user gets id=2, not id=1
        cur = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='users'").fetchone()
        if cur is None:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('users', 1)")
        elif cur["seq"] < 1:
            conn.execute("UPDATE sqlite_sequence SET seq=1 WHERE name='users'")
        print(f"  Inserted users.id=1 email={email_lc}")

    # Audit row — useful for "when did this migration run?" later
    try:
        conn.execute(
            "INSERT INTO broker_key_events (user_id, broker, event_type, detail) "
            "VALUES (1, 'system', 'operator_seeded', ?)",
            (f"email={email_lc}, password_hash_prefix={password_hash[:8]}...",),
        )
    except sqlite3.Error as e:
        print(f"  (note: failed to log audit event: {e})")

    conn.commit()
    conn.close()
    return 1


def upsert_demo_user(
    *,
    db_path:        str,
    email:          str,
    password_hash:  str,
    dry_run:        bool = False,
) -> int:
    """Insert or update user_id=2 as the demo user (is_admin=0).

    Same idempotent contract as upsert_operator. The demo account is a
    standard global.db user with a known email; downstream code (get_db
    in api/main.py) recognizes it by email and switches to demo_data.db
    when shadow_mode is off. This way the new auth router has no demo
    carve-out — demo logs in like any other user.

    Returns the upserted user's id (always 2).
    """
    if not Path(db_path).exists():
        print(f"FATAL: global.db not found at {db_path}", file=sys.stderr)
        raise SystemExit(2)

    email_lc = email.strip().lower()

    if dry_run:
        print(f"  [dry-run] Would upsert users.id=2 email={email_lc} (demo)")
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    existing = conn.execute("SELECT id, email FROM users WHERE id = 2").fetchone()
    if existing:
        conn.execute(
            "UPDATE users SET email=?, password_hash=?, is_admin=0, email_verified=1, "
            "updated_at=CURRENT_TIMESTAMP, deleted_at=NULL WHERE id=2",
            (email_lc, password_hash),
        )
        print(f"  Updated existing users.id=2 (demo, was email={existing['email']})")
    else:
        # Reject if email already attached to a different user_id
        clash = conn.execute(
            "SELECT id FROM users WHERE email=? AND deleted_at IS NULL",
            (email_lc,),
        ).fetchone()
        if clash and clash["id"] != 2:
            conn.close()
            print(f"FATAL: demo email {email_lc} already in use by user_id={clash['id']}", file=sys.stderr)
            raise SystemExit(3)
        conn.execute(
            "INSERT INTO users (id, email, password_hash, is_admin, email_verified) "
            "VALUES (2, ?, ?, 0, 1)",
            (email_lc, password_hash),
        )
        # Bump sqlite_sequence so the next signup gets id=3
        cur = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='users'").fetchone()
        if cur is None:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('users', 2)")
        elif cur["seq"] < 2:
            conn.execute("UPDATE sqlite_sequence SET seq=2 WHERE name='users'")
        print(f"  Inserted users.id=2 email={email_lc} (demo)")

    try:
        conn.execute(
            "INSERT INTO broker_key_events (user_id, broker, event_type, detail) "
            "VALUES (2, 'system', 'demo_user_seeded', ?)",
            (f"email={email_lc}",),
        )
    except sqlite3.Error as e:
        print(f"  (note: failed to log audit event: {e})")

    conn.commit()
    conn.close()
    return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Force id=1 explicitly via INSERT, fall back to UPDATE if conflict
    existing = conn.execute("SELECT id, email FROM users WHERE id = 1").fetchone()
    if existing:
        conn.execute(
            "UPDATE users SET email=?, password_hash=?, is_admin=1, email_verified=1, "
            "updated_at=CURRENT_TIMESTAMP, deleted_at=NULL WHERE id=1",
            (email_lc, password_hash),
        )
        print(f"  Updated existing users.id=1 (was email={existing['email']})")
    else:
        # Make sure nobody else holds this email
        clash = conn.execute(
            "SELECT id FROM users WHERE email=? AND deleted_at IS NULL",
            (email_lc,),
        ).fetchone()
        if clash and clash["id"] != 1:
            conn.close()
            print(f"FATAL: email {email_lc} already in use by user_id={clash['id']}", file=sys.stderr)
            raise SystemExit(3)
        conn.execute(
            "INSERT INTO users (id, email, password_hash, is_admin, email_verified) "
            "VALUES (1, ?, ?, 1, 1)",
            (email_lc, password_hash),
        )
        # Bump sqlite_sequence so the next new user gets id=2, not id=1
        cur = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='users'").fetchone()
        if cur is None:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('users', 1)")
        elif cur["seq"] < 1:
            conn.execute("UPDATE sqlite_sequence SET seq=1 WHERE name='users'")
        print(f"  Inserted users.id=1 email={email_lc}")

    # Audit row — useful for "when did this migration run?" later
    try:
        conn.execute(
            "INSERT INTO broker_key_events (user_id, broker, event_type, detail) "
            "VALUES (1, 'system', 'operator_seeded', ?)",
            (f"email={email_lc}, password_hash_prefix={password_hash[:8]}...",),
        )
    except sqlite3.Error as e:
        # Don't fail the whole migration on an audit-log hiccup
        print(f"  (note: failed to log audit event: {e})")

    conn.commit()
    conn.close()
    return 1


def symlink_user_config(
    *,
    user_id:       int,
    user_data_dir: str,
    config_path:   str,
    dry_run:       bool = False,
) -> Path:
    """Create user_data_dir/<user_id>/Config.yaml as a symlink to config_path.

    Idempotent — if the symlink already exists and points where we want, do nothing.
    If a REAL file already exists at the target path, refuse to overwrite (would
    indicate the provisioner has already taken over and we should not stomp it).

    Returns the symlink path.
    """
    user_dir = Path(user_data_dir) / str(user_id)
    link_path = user_dir / "Config.yaml"
    target = Path(config_path).resolve()

    if dry_run:
        print(f"  [dry-run] Would symlink {link_path} → {target}")
        return link_path

    user_dir.mkdir(parents=True, exist_ok=True)

    if link_path.exists() and not link_path.is_symlink():
        # Real file already in place — provisioner-style layout. Don't touch.
        print(f"  Skipped symlink: {link_path} already exists as a real file.")
        return link_path

    if link_path.is_symlink():
        current_target = link_path.resolve()
        if current_target == target:
            print(f"  Symlink already in place: {link_path} → {target}")
            return link_path
        # Different target — replace it
        link_path.unlink()

    if not target.exists():
        print(f"WARNING: target {target} does not exist; symlink created but dangling.", file=sys.stderr)

    os.symlink(target, link_path)
    print(f"  Symlinked {link_path} → {target}")
    return link_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the operator + demo user in global.db")
    parser.add_argument("--email", required=True, help="Email to register the operator (user_id=1) under")
    parser.add_argument(
        "--password-hash",
        default=os.environ.get("API_PASSWORD_HASH", ""),
        help="bcrypt or argon2 hash for the operator (default: $API_PASSWORD_HASH)",
    )
    parser.add_argument(
        "--demo-email",
        default=os.environ.get("DEMO_EMAIL", "demo@foundationbots.com"),
        help="Email for the demo user (user_id=2). Default: demo@foundationbots.com",
    )
    parser.add_argument(
        "--demo-password-hash",
        default=os.environ.get("DEMO_PASSWORD_HASH", ""),
        help="bcrypt or argon2 hash for the demo user (default: $DEMO_PASSWORD_HASH)",
    )
    parser.add_argument("--skip-demo", action="store_true",
                        help="Skip seeding the demo user (just do the operator)")
    parser.add_argument("--db-path", default=_detect_default_db_path())
    parser.add_argument("--user-data-dir", default=_detect_default_user_data_dir())
    parser.add_argument("--config-path", default=_detect_default_config_path())
    parser.add_argument("--skip-symlink", action="store_true",
                        help="Don't create the per-user Config.yaml symlink")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.password_hash:
        print(
            "FATAL: --password-hash not given and API_PASSWORD_HASH env is unset. "
            "Pass --password-hash explicitly or set the env var.",
            file=sys.stderr,
        )
        return 1

    if not args.skip_demo and not args.demo_password_hash:
        print(
            "FATAL: --demo-password-hash not given and DEMO_PASSWORD_HASH env is unset. "
            "Either pass it, set the env, or use --skip-demo.",
            file=sys.stderr,
        )
        return 1

    print(f"Migrating operator → user_id=1 in {args.db_path}")
    print(f"  Email:        {args.email}")
    print(f"  Hash format:  {args.password_hash[:6]}... ({len(args.password_hash)} chars)")
    print(f"  User dir:     {args.user_data_dir}")
    print(f"  Config link:  {args.config_path}")
    if args.dry_run:
        print("  [DRY RUN — no changes]")

    user_id = upsert_operator(
        db_path        = args.db_path,
        email          = args.email,
        password_hash  = args.password_hash,
        dry_run        = args.dry_run,
    )

    if not args.skip_symlink:
        symlink_user_config(
            user_id        = user_id,
            user_data_dir  = args.user_data_dir,
            config_path    = args.config_path,
            dry_run        = args.dry_run,
        )

    if not args.skip_demo:
        print(f"\nSeeding demo user → user_id=2")
        print(f"  Email:        {args.demo_email}")
        print(f"  Hash format:  {args.demo_password_hash[:6]}... ({len(args.demo_password_hash)} chars)")
        upsert_demo_user(
            db_path        = args.db_path,
            email          = args.demo_email,
            password_hash  = args.demo_password_hash,
            dry_run        = args.dry_run,
        )

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
