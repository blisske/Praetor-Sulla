"""Tests for scripts/migrate_operator_to_user_1.py.

The migration is the cutover step between single-tenant and multi-tenant
auth — gets exercised once in production. Worth a thorough test for that
reason alone.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_migrate_operator -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import init_global_db
import migrate_operator_to_user_1 as migrate


# Sample bcrypt hash for "test" — looks like a real one to make sure the
# script doesn't reject by format. (Hash function not actually called.)
SAMPLE_BCRYPT = "$2b$12$KIXqQfNn5L8L1.gO8L8L8eL8L1.gO8L8L8L8L1.gO8L8L8L8L8L8L8"


class MigrationTestBase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foundation_migrate_test_")
        self.db_path = os.path.join(self.tmpdir, "test_global.db")
        init_global_db.init_global_db(self.db_path, verbose=False)

        self.user_data_dir = os.path.join(self.tmpdir, "users")
        os.makedirs(self.user_data_dir)

        # Real Config.yaml to symlink to
        self.config_path = os.path.join(self.tmpdir, "Config.yaml")
        Path(self.config_path).write_text("exchange:\n  shadow_mode: true\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ─── Upsert ────────────────────────────────────────────────────────────────


class UpsertOperatorTests(MigrationTestBase):

    def test_inserts_new_user_at_id_1(self):
        uid = migrate.upsert_operator(
            db_path=self.db_path, email="op@example.com", password_hash=SAMPLE_BCRYPT,
        )
        self.assertEqual(uid, 1)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT id, email, is_admin, email_verified FROM users WHERE id=1").fetchone()
        conn.close()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "op@example.com")
        self.assertEqual(row[2], 1)  # is_admin
        self.assertEqual(row[3], 1)  # email_verified

    def test_email_normalized_to_lowercase(self):
        migrate.upsert_operator(
            db_path=self.db_path, email="OpERator@Example.COM", password_hash=SAMPLE_BCRYPT,
        )
        conn = sqlite3.connect(self.db_path)
        email = conn.execute("SELECT email FROM users WHERE id=1").fetchone()[0]
        conn.close()
        self.assertEqual(email, "operator@example.com")

    def test_idempotent_rerun_updates(self):
        """Running twice with different email updates in place (no duplicate)."""
        migrate.upsert_operator(
            db_path=self.db_path, email="first@example.com", password_hash=SAMPLE_BCRYPT,
        )
        migrate.upsert_operator(
            db_path=self.db_path, email="second@example.com", password_hash="$2b$12$NEWHASH",
        )
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT id, email, password_hash FROM users ORDER BY id").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 1)
        self.assertEqual(rows[0][1], "second@example.com")
        self.assertEqual(rows[0][2], "$2b$12$NEWHASH")

    def test_subsequent_user_gets_id_2(self):
        """Operator at id=1 should not collide with future signup ids."""
        migrate.upsert_operator(
            db_path=self.db_path, email="op@example.com", password_hash=SAMPLE_BCRYPT,
        )
        # Simulate a regular signup using the auth helper
        from core import auth as core_auth
        core_auth.GLOBAL_DB_PATH = self.db_path
        new_uid = core_auth.create_user(email="other@example.com", password="longenoughpassword")
        self.assertEqual(new_uid, 2)

    def test_clash_with_other_id_aborts(self):
        """If email is already in use by user_id != 1, migration refuses."""
        from core import auth as core_auth
        core_auth.GLOBAL_DB_PATH = self.db_path
        core_auth.create_user(email="conflict@example.com", password="longenoughpassword")  # id=1 here
        # Now insert another user at id=2 with id=1 still free
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM users WHERE email='conflict@example.com'")
        conn.commit()
        conn.close()
        core_auth.create_user(email="someone@example.com", password="longenoughpassword")  # id=2
        with self.assertRaises(SystemExit) as ctx:
            migrate.upsert_operator(
                db_path=self.db_path, email="someone@example.com", password_hash=SAMPLE_BCRYPT,
            )
        self.assertEqual(ctx.exception.code, 3)

    def test_missing_db_aborts(self):
        with self.assertRaises(SystemExit) as ctx:
            migrate.upsert_operator(
                db_path="/nonexistent/global.db",
                email="op@example.com",
                password_hash=SAMPLE_BCRYPT,
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_dry_run_writes_nothing(self):
        migrate.upsert_operator(
            db_path=self.db_path, email="op@example.com", password_hash=SAMPLE_BCRYPT,
            dry_run=True,
        )
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_audit_event_logged(self):
        migrate.upsert_operator(
            db_path=self.db_path, email="op@example.com", password_hash=SAMPLE_BCRYPT,
        )
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT event_type, detail FROM broker_key_events WHERE user_id=1"
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "operator_seeded")
        # password_hash prefix in audit, but truncated — no full hash leak
        self.assertNotIn(SAMPLE_BCRYPT, rows[0][1])


# ─── Symlink ───────────────────────────────────────────────────────────────


class SymlinkUserConfigTests(MigrationTestBase):

    def test_creates_symlink_to_target(self):
        link = migrate.symlink_user_config(
            user_id=1, user_data_dir=self.user_data_dir, config_path=self.config_path,
        )
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), Path(self.config_path).resolve())

    def test_idempotent_when_already_pointing_correctly(self):
        migrate.symlink_user_config(
            user_id=1, user_data_dir=self.user_data_dir, config_path=self.config_path,
        )
        # Run again — should be a no-op
        link = migrate.symlink_user_config(
            user_id=1, user_data_dir=self.user_data_dir, config_path=self.config_path,
        )
        self.assertTrue(link.is_symlink())

    def test_replaces_symlink_if_pointing_elsewhere(self):
        link_path = Path(self.user_data_dir) / "1" / "Config.yaml"
        link_path.parent.mkdir(parents=True)
        other_target = Path(self.tmpdir) / "Other.yaml"
        other_target.write_text("dummy")
        os.symlink(other_target, link_path)

        migrate.symlink_user_config(
            user_id=1, user_data_dir=self.user_data_dir, config_path=self.config_path,
        )
        self.assertEqual(link_path.resolve(), Path(self.config_path).resolve())

    def test_skips_when_real_file_already_present(self):
        """Provisioner has already taken over — don't stomp the real file."""
        user_dir = Path(self.user_data_dir) / "1"
        user_dir.mkdir(parents=True)
        existing = user_dir / "Config.yaml"
        existing.write_text("real-content-from-provisioner: true\n")

        migrate.symlink_user_config(
            user_id=1, user_data_dir=self.user_data_dir, config_path=self.config_path,
        )
        # Still a real file, not a symlink
        self.assertFalse(existing.is_symlink())
        self.assertIn("provisioner", existing.read_text())

    def test_dry_run_creates_nothing(self):
        migrate.symlink_user_config(
            user_id=1, user_data_dir=self.user_data_dir, config_path=self.config_path,
            dry_run=True,
        )
        self.assertFalse((Path(self.user_data_dir) / "1" / "Config.yaml").exists())

    def test_mode_endpoint_can_write_through_symlink(self):
        """End-to-end check: after migration, /api/user/mode-style YAML edits
        flow through the symlink and update the engine-visible Config.yaml."""
        migrate.symlink_user_config(
            user_id=1, user_data_dir=self.user_data_dir, config_path=self.config_path,
        )
        from ruamel.yaml import YAML
        yaml = YAML()
        link_path = Path(self.user_data_dir) / "1" / "Config.yaml"
        with open(link_path, "r") as f:
            data = yaml.load(f)
        data["exchange"]["shadow_mode"] = False
        with open(link_path, "w") as f:
            yaml.dump(data, f)
        # Direct read of the underlying file should reflect the change
        with open(self.config_path, "r") as f:
            target_text = f.read()
        self.assertIn("shadow_mode: false", target_text)


# ─── Demo user seeding ─────────────────────────────────────────────────────


class UpsertDemoUserTests(MigrationTestBase):

    def test_inserts_demo_at_id_2(self):
        # Operator first so the sequence is set up
        migrate.upsert_operator(
            db_path=self.db_path, email="op@x.com", password_hash=SAMPLE_BCRYPT,
        )
        uid = migrate.upsert_demo_user(
            db_path=self.db_path, email="demo@x.com", password_hash=SAMPLE_BCRYPT,
        )
        self.assertEqual(uid, 2)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT id, email, is_admin, email_verified FROM users WHERE id=2").fetchone()
        conn.close()
        self.assertEqual(row[0], 2)
        self.assertEqual(row[1], "demo@x.com")
        self.assertEqual(row[2], 0)  # is_admin=0 for demo
        self.assertEqual(row[3], 1)  # email_verified=1

    def test_idempotent_rerun(self):
        migrate.upsert_operator(
            db_path=self.db_path, email="op@x.com", password_hash=SAMPLE_BCRYPT,
        )
        migrate.upsert_demo_user(
            db_path=self.db_path, email="demo@x.com", password_hash=SAMPLE_BCRYPT,
        )
        migrate.upsert_demo_user(
            db_path=self.db_path, email="demo2@x.com", password_hash="$2b$12$NEW",
        )
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT id, email FROM users WHERE id=2").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "demo2@x.com")

    def test_subsequent_signup_gets_id_3(self):
        migrate.upsert_operator(
            db_path=self.db_path, email="op@x.com", password_hash=SAMPLE_BCRYPT,
        )
        migrate.upsert_demo_user(
            db_path=self.db_path, email="demo@x.com", password_hash=SAMPLE_BCRYPT,
        )
        from core import auth as core_auth
        core_auth.GLOBAL_DB_PATH = self.db_path
        new_uid = core_auth.create_user(email="alice@x.com", password="longenoughpassword")
        self.assertEqual(new_uid, 3)

    def test_dry_run_writes_nothing(self):
        migrate.upsert_demo_user(
            db_path=self.db_path, email="demo@x.com", password_hash=SAMPLE_BCRYPT,
            dry_run=True,
        )
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


# ─── CLI smoke ─────────────────────────────────────────────────────────────


class CLISmokeTests(MigrationTestBase):

    def test_main_happy_path(self):
        argv = [
            "--email", "blisske@example.com",
            "--password-hash", SAMPLE_BCRYPT,
            "--demo-password-hash", SAMPLE_BCRYPT,
            "--db-path", self.db_path,
            "--user-data-dir", self.user_data_dir,
            "--config-path", self.config_path,
        ]
        rc = migrate.main(argv)
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(self.db_path)
        op_email = conn.execute("SELECT email FROM users WHERE id=1").fetchone()[0]
        demo_email = conn.execute("SELECT email FROM users WHERE id=2").fetchone()[0]
        conn.close()
        self.assertEqual(op_email, "blisske@example.com")
        self.assertEqual(demo_email, "demo@foundationbots.com")
        self.assertTrue((Path(self.user_data_dir) / "1" / "Config.yaml").is_symlink())

    def test_main_dry_run_changes_nothing(self):
        argv = [
            "--email", "blisske@example.com",
            "--password-hash", SAMPLE_BCRYPT,
            "--demo-password-hash", SAMPLE_BCRYPT,
            "--db-path", self.db_path,
            "--user-data-dir", self.user_data_dir,
            "--config-path", self.config_path,
            "--dry-run",
        ]
        rc = migrate.main(argv)
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_main_no_password_hash_fails(self):
        # Clear env so it can't fall back
        saved = os.environ.pop("API_PASSWORD_HASH", None)
        saved_demo = os.environ.pop("DEMO_PASSWORD_HASH", None)
        try:
            argv = [
                "--email", "x@example.com",
                "--demo-password-hash", SAMPLE_BCRYPT,
                "--db-path", self.db_path,
                "--user-data-dir", self.user_data_dir,
                "--config-path", self.config_path,
            ]
            rc = migrate.main(argv)
            self.assertEqual(rc, 1)
        finally:
            if saved:
                os.environ["API_PASSWORD_HASH"] = saved
            if saved_demo:
                os.environ["DEMO_PASSWORD_HASH"] = saved_demo

    def test_main_skip_demo_no_demo_hash_needed(self):
        saved = os.environ.pop("DEMO_PASSWORD_HASH", None)
        try:
            argv = [
                "--email", "x@example.com",
                "--password-hash", SAMPLE_BCRYPT,
                "--db-path", self.db_path,
                "--user-data-dir", self.user_data_dir,
                "--config-path", self.config_path,
                "--skip-demo",
            ]
            rc = migrate.main(argv)
            self.assertEqual(rc, 0)
            conn = sqlite3.connect(self.db_path)
            n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            conn.close()
            self.assertEqual(n, 1)  # only operator, no demo
        finally:
            if saved:
                os.environ["DEMO_PASSWORD_HASH"] = saved

    def test_main_skip_symlink_flag(self):
        argv = [
            "--email", "x@example.com",
            "--password-hash", SAMPLE_BCRYPT,
            "--demo-password-hash", SAMPLE_BCRYPT,
            "--db-path", self.db_path,
            "--user-data-dir", self.user_data_dir,
            "--config-path", self.config_path,
            "--skip-symlink",
        ]
        rc = migrate.main(argv)
        self.assertEqual(rc, 0)
        self.assertFalse((Path(self.user_data_dir) / "1" / "Config.yaml").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
