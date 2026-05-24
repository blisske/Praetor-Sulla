"""Tests for core/provisioner_client.py — API-side helpers that signup/
delete handlers call to request engine provisioning from the host daemon.

All filesystem state lives in a temp dir per test class. No Docker involved.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_provisioner_client -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import provisioner_client as pc


class ProvisionerClientTestBase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foundation_pc_test_")
        self.user_dir   = Path(self.tmpdir) / "users"
        self.tmpl_dir   = Path(self.tmpdir) / "template"
        self.queue_dir  = self.user_dir / "_queue"

        # Materialize a valid template so initialize_user_dir succeeds
        self.tmpl_dir.mkdir(parents=True)
        (self.tmpl_dir / "ionic.db").write_bytes(b"\x00" * 32)
        (self.tmpl_dir / "Config.yaml").write_text(
            "exchange:\n  shadow_mode: true\n  venue: oanda\n"
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ─── initialize_user_dir ───────────────────────────────────────────────────


class InitializeUserDirTests(ProvisionerClientTestBase):

    def _init(self, user_id: int, **overrides):
        kwargs = dict(
            user_data_dir = str(self.user_dir),
            template_dir  = str(self.tmpl_dir),
            queue_dir     = str(self.queue_dir),
        )
        kwargs.update(overrides)
        return pc.initialize_user_dir(user_id, **kwargs)

    def test_creates_user_dir_with_template_files(self):
        path = self._init(42)
        self.assertTrue(path.exists())
        self.assertTrue((path / "ionic.db").exists())
        self.assertTrue((path / "Config.yaml").exists())
        self.assertTrue((path / ".engine_heartbeat").exists())
        self.assertEqual(path, self.user_dir / "42")

    def test_template_db_copied_verbatim(self):
        self._init(7)
        src = (self.tmpl_dir / "ionic.db").read_bytes()
        dst = (self.user_dir / "7" / "ionic.db").read_bytes()
        self.assertEqual(src, dst)

    def test_config_yaml_copied_verbatim(self):
        self._init(8)
        src = (self.tmpl_dir / "Config.yaml").read_text()
        dst = (self.user_dir / "8" / "Config.yaml").read_text()
        self.assertEqual(src, dst)

    def test_missing_template_db_raises(self):
        (self.tmpl_dir / "ionic.db").unlink()
        with self.assertRaises(pc.ProvisionerError) as ctx:
            self._init(9)
        self.assertIn("Template DB missing", str(ctx.exception))

    def test_missing_template_config_raises(self):
        (self.tmpl_dir / "Config.yaml").unlink()
        with self.assertRaises(pc.ProvisionerError) as ctx:
            self._init(10)
        self.assertIn("Template Config.yaml missing", str(ctx.exception))

    def test_existing_user_dir_refuses_without_overwrite(self):
        self._init(11)
        with self.assertRaises(pc.ProvisionerError) as ctx:
            self._init(11)
        self.assertIn("already exists", str(ctx.exception))

    def test_overwrite_true_replaces_contents(self):
        self._init(12)
        # Mutate the existing files so we can prove they were overwritten
        (self.user_dir / "12" / "Config.yaml").write_text("OLD CONTENTS")
        (self.user_dir / "12" / "extra.txt").write_text("should be gone")

        self._init(12, overwrite=True)

        self.assertNotEqual(
            (self.user_dir / "12" / "Config.yaml").read_text(),
            "OLD CONTENTS",
        )
        self.assertFalse((self.user_dir / "12" / "extra.txt").exists())

    def test_creates_queue_dir_if_missing(self):
        # queue_dir doesn't exist yet
        self.assertFalse(self.queue_dir.exists())
        self._init(13)
        self.assertTrue(self.queue_dir.exists())


# ─── enqueue_provision / enqueue_teardown ──────────────────────────────────


class EnqueueFlagTests(ProvisionerClientTestBase):

    def test_provision_flag_written(self):
        flag = pc.enqueue_provision(99, queue_dir=str(self.queue_dir))
        self.assertEqual(flag, self.queue_dir / "99.provision")
        self.assertTrue(flag.exists())

    def test_teardown_flag_written(self):
        flag = pc.enqueue_teardown(101, queue_dir=str(self.queue_dir))
        self.assertEqual(flag, self.queue_dir / "101.teardown")
        self.assertTrue(flag.exists())

    def test_queue_dir_created_if_missing(self):
        self.assertFalse(self.queue_dir.exists())
        pc.enqueue_provision(1, queue_dir=str(self.queue_dir))
        self.assertTrue(self.queue_dir.exists())

    def test_reprovision_idempotent_overwrites_flag(self):
        pc.enqueue_provision(5, queue_dir=str(self.queue_dir))
        first_mtime = (self.queue_dir / "5.provision").stat().st_mtime
        time.sleep(0.05)
        pc.enqueue_provision(5, queue_dir=str(self.queue_dir))
        second_mtime = (self.queue_dir / "5.provision").stat().st_mtime
        # touch() bumps mtime even when the file exists
        self.assertGreaterEqual(second_mtime, first_mtime)


# ─── Status helpers ────────────────────────────────────────────────────────


class StatusHelperTests(ProvisionerClientTestBase):

    def _init(self, user_id):
        pc.initialize_user_dir(
            user_id,
            user_data_dir = str(self.user_dir),
            template_dir  = str(self.tmpl_dir),
            queue_dir     = str(self.queue_dir),
        )

    def test_user_dir_exists_false_before_init(self):
        self.assertFalse(pc.user_dir_exists(42, user_data_dir=str(self.user_dir)))

    def test_user_dir_exists_true_after_init(self):
        self._init(42)
        self.assertTrue(pc.user_dir_exists(42, user_data_dir=str(self.user_dir)))

    def test_read_engine_heartbeat_returns_none_if_missing(self):
        self.assertIsNone(pc.read_engine_heartbeat(99, user_data_dir=str(self.user_dir)))

    def test_read_engine_heartbeat_returns_datetime_after_init(self):
        self._init(99)
        hb = pc.read_engine_heartbeat(99, user_data_dir=str(self.user_dir))
        self.assertIsNotNone(hb)
        self.assertIsInstance(hb, datetime)
        # Heartbeat is fresh — within the last 60 seconds, in UTC
        now = datetime.now(timezone.utc)
        delta = (now - hb).total_seconds()
        self.assertLess(delta, 60)
        self.assertGreaterEqual(delta, 0)

    def test_provision_pending_reflects_flag(self):
        self.assertFalse(pc.is_provision_pending(1, queue_dir=str(self.queue_dir)))
        pc.enqueue_provision(1, queue_dir=str(self.queue_dir))
        self.assertTrue(pc.is_provision_pending(1, queue_dir=str(self.queue_dir)))

    def test_teardown_pending_reflects_flag(self):
        self.assertFalse(pc.is_teardown_pending(1, queue_dir=str(self.queue_dir)))
        pc.enqueue_teardown(1, queue_dir=str(self.queue_dir))
        self.assertTrue(pc.is_teardown_pending(1, queue_dir=str(self.queue_dir)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
